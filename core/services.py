"""ビジネスロジック層。

ビューから業務処理を分離する。売上記録などDB更新を伴う処理は
transaction.atomic() でまとめ、機密データ（帳簿）の整合性を担保する。
金額はすべて Decimal（整数円）で扱い、float は使わない。
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import CashEntry, Expense, Product, Sale, SaleItem, StockAdjustment


class SaleValidationError(ValidationError):
    """売上入力のバリデーションエラー。"""


class ExpenseValidationError(ValidationError):
    """経費入力のバリデーションエラー。"""


class StockValidationError(ValidationError):
    """在庫調整のバリデーションエラー。"""


# 許可値（choices の value 集合）。未知の値は拒否する。
ALLOWED_EXPENSE_CATEGORIES = frozenset(c.value for c in Expense.Category)
ALLOWED_ADJUSTMENT_KINDS = frozenset(c.value for c in StockAdjustment.Kind)

# amount フィールドは max_digits=12（整数円）。上限を超える値は拒否。
_MAX_AMOUNT = Decimal(10) ** 12
_MAX_MEMO_LEN = 200


def _normalize_items(stall, items):
    """入力アイテムを検証し {Product: quantity} に正規化する。

    items: [{"id": <product_id>, "qty": <quantity>}, ...] 相当の反復可能。
    - quantity は 1 以上の整数であること
    - product はこの stall に属し、有効（is_active）であること
      → 他 stall の product_id を混ぜられても弾く（オブジェクトレベルの検証）
    """
    if not items:
        raise SaleValidationError("会計に商品がありません。")

    # 数量を product_id ごとに集計（重複IDは加算）
    qty_by_id: dict[int, int] = {}
    for raw in items:
        try:
            pid = int(raw["id"])
            qty = int(raw["qty"])
        except (KeyError, TypeError, ValueError):
            raise SaleValidationError("商品データの形式が不正です。")
        if qty <= 0:
            raise SaleValidationError("数量は1以上の整数で指定してください。")
        qty_by_id[pid] = qty_by_id.get(pid, 0) + qty

    # この stall に属する有効な商品だけを取得
    products = {
        p.id: p
        for p in Product.objects.filter(
            id__in=qty_by_id.keys(), stall=stall, is_active=True
        )
    }
    missing = set(qty_by_id) - set(products)
    if missing:
        # 他 stall の商品や存在しない商品が混入 → 拒否
        raise SaleValidationError("この屋台で扱っていない商品が含まれています。")

    return {products[pid]: qty for pid, qty in qty_by_id.items()}


@transaction.atomic
def record_sale(stall, items) -> Sale:
    """会計トレイの内容を1件の売上として記録する。

    - Sale を作成
    - 各明細を SaleItem として作成（unit_price は SaleItem.save() が現在価格を
      自動スナップショット）
    - Sale.recalc_total() で合計を確定
    - 現金売上として CashEntry(entry_type=SALE, amount=+total) を記録
      （Stall.cash_balance に反映される）

    在庫超過（remaining_stock を超える数量）は「記録可（警告に留める）」方針。
    サーバ側ではブロックせず記録する。売切（残0）ボタンのみ UI 側で無効化する。

    戻り値: 作成された Sale
    """
    normalized = _normalize_items(stall, items)

    sale = Sale.objects.create(stall=stall)
    for product, qty in normalized.items():
        # unit_price は渡さない → SaleItem.save() が product.price をスナップショット
        SaleItem.objects.create(sale=sale, product=product, quantity=qty)

    total = sale.recalc_total(save=True)

    CashEntry.objects.create(
        stall=stall,
        entry_type=CashEntry.EntryType.SALE,
        amount=total,  # 入金は正
        sale=sale,
    )
    return sale


def _validate_amount(raw) -> Decimal:
    """金額を検証し、正の整数円 Decimal に正規化する。float は使わない。"""
    if isinstance(raw, float):
        # float は精度が信用できないため受け付けない
        raise ExpenseValidationError("金額の形式が不正です。")
    try:
        amount = Decimal(str(raw).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ExpenseValidationError("金額の形式が不正です。")
    if amount != amount.to_integral_value():
        raise ExpenseValidationError("金額は整数（円）で入力してください。")
    amount = amount.quantize(Decimal("1"))
    if amount <= 0:
        raise ExpenseValidationError("金額は1円以上で入力してください。")
    if amount >= _MAX_AMOUNT:
        raise ExpenseValidationError("金額が大きすぎます。")
    return amount


def _clean_note(note) -> str:
    note = (note or "").strip()
    if len(note) > _MAX_MEMO_LEN:
        raise ExpenseValidationError("メモが長すぎます。")
    return note


@transaction.atomic
def record_expense(stall, amount, category, note="", paid_in_cash=True) -> Expense:
    """経費・仕入れを1件記録する。

    - amount は正の整数円（Decimal）に正規化して検証
    - category は Expense.Category の許可値のみ
    - Expense を作成
    - 現金払い（paid_in_cash=True）なら CashEntry(entry_type=EXPENSE, amount=-amount)
      を記録して Stall.cash_balance に反映（出金は負）

    戻り値: 作成された Expense
    """
    amount = _validate_amount(amount)
    if category not in ALLOWED_EXPENSE_CATEGORIES:
        raise ExpenseValidationError("カテゴリの指定が不正です。")
    note = _clean_note(note)

    expense = Expense.objects.create(
        stall=stall,
        amount=amount,
        category=category,
        memo=note,
        paid_in_cash=bool(paid_in_cash),
    )
    if paid_in_cash:
        CashEntry.objects.create(
            stall=stall,
            entry_type=CashEntry.EntryType.EXPENSE,
            amount=-amount,  # 出金は負
            expense=expense,
        )
    return expense


@transaction.atomic
def record_stock_adjustment(product, delta, kind, note="") -> StockAdjustment:
    """在庫の増減を台帳（StockAdjustment）に1件記録する。

    - product は在庫管理対象（is_stock_managed=True）であること
    - delta は 0 以外の整数（補充は正、棚卸は差分）
    - kind は StockAdjustment.Kind の許可値のみ

    戻り値: 作成された StockAdjustment
    """
    if not product.is_stock_managed:
        raise StockValidationError("この商品は在庫管理の対象外です。")
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        raise StockValidationError("増減数の形式が不正です。")
    if delta == 0:
        raise StockValidationError("在庫の変更がありません。")
    if kind not in ALLOWED_ADJUSTMENT_KINDS:
        raise StockValidationError("在庫調整の種別が不正です。")
    note = _clean_note(note)

    return StockAdjustment.objects.create(
        product=product,
        delta=delta,
        kind=kind,
        note=note,
    )


@transaction.atomic
def void_sale(sale: Sale) -> None:
    """売上を取り消す。紐づくCashEntryも削除し、現金残高のズレを防ぐ。"""
    CashEntry.objects.filter(sale=sale).delete()
    sale.delete()  # SaleItem は CASCADE で自動削除 → 在庫は自動で戻る


@transaction.atomic
def void_expense(expense: Expense) -> None:
    """経費を取り消す。紐づくCashEntry（現金払いの場合のみ存在）も削除する。"""
    CashEntry.objects.filter(expense=expense).delete()
    expense.delete()


@transaction.atomic
def void_stock_adjustment(adjustment: StockAdjustment) -> None:
    """在庫調整（補充・棚卸）を取り消す。"""
    adjustment.delete()
