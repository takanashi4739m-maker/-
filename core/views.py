"""屋台運営画面のビュー。

アクセス制御:
- 屋台画面は URL の access_token に一致する Stall を get_object_or_404 で取得。
- ダッシュボードは Event.dashboard_token で取得。
- トークンが無効なら 404（オブジェクトレベルのアクセス制御）。
"""

import json
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Event, Expense, Sale, Stall, StockAdjustment
from .services import (
    record_expense,
    record_sale,
    record_stock_adjustment,
    void_expense,
    void_sale,
    void_stock_adjustment,
)


def _get_stall(access_token: str) -> Stall:
    """トークンに一致する屋台を取得。無効なら 404。"""
    return get_object_or_404(Stall, access_token=access_token)


# ===== 売上入力 =====

def sale_input(request, access_token):
    """売上入力画面（GET: 描画 / POST: 記録→PRGでリダイレクト）。"""
    stall = _get_stall(access_token)

    if request.method == "POST":
        return _handle_sale_post(request, stall, access_token)

    products = list(stall.products.filter(is_active=True))
    context = {
        "stall": stall,
        "products": products,
        "active_tab": "sale",
    }
    return render(request, "core/sale_input.html", context)


def _handle_sale_post(request, stall, access_token):
    """会計トレイの内容を受け取り、売上を記録する。"""
    raw = request.POST.get("items", "")
    try:
        items = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        items = None

    if not isinstance(items, list) or not items:
        messages.error(request, "会計に商品が追加されていません。")
        return redirect("core:sale_input", access_token=access_token)

    try:
        sale = record_sale(stall, items)
    except ValidationError as e:
        messages.error(request, "；".join(e.messages))
        return redirect("core:sale_input", access_token=access_token)

    # PRG: 記録成功後は同じ画面へリダイレクト。トーストで金額を表示。
    messages.success(request, f"¥{sale.total:,.0f} を記録しました")
    return redirect("core:sale_input", access_token=access_token)


# ===== 経費・仕入れ入力 =====

def expense_input(request, access_token):
    """経費入力画面（GET: 描画 / POST: 記録→PRG）。"""
    stall = _get_stall(access_token)

    if request.method == "POST":
        return _handle_expense_post(request, stall, access_token)

    context = {
        "stall": stall,
        "categories": Expense.Category.choices,
        "active_tab": "expense",
    }
    return render(request, "core/expense.html", context)


def _handle_expense_post(request, stall, access_token):
    amount = request.POST.get("amount", "")
    category = request.POST.get("category", "")
    note = request.POST.get("note", "")
    # 現金払いは既定 True。チェックボックス未送信＝現金払い。
    paid_in_cash = request.POST.get("paid_in_cash", "1") != "0"

    try:
        expense = record_expense(
            stall, amount, category, note=note, paid_in_cash=paid_in_cash
        )
    except ValidationError as e:
        messages.error(request, "；".join(e.messages))
        return redirect("core:expense_input", access_token=access_token)

    messages.success(request, f"¥{expense.amount:,.0f} を記録しました")
    return redirect("core:expense_input", access_token=access_token)


# ===== 在庫管理（台帳方式） =====

def stock_manage(request, access_token):
    """在庫画面（GET: 一覧 / POST: 補充・棚卸を台帳に記録→PRG）。"""
    stall = _get_stall(access_token)

    if request.method == "POST":
        return _handle_stock_post(request, stall, access_token)

    products = list(stall.products.filter(is_active=True))
    managed, unmanaged = [], []
    for p in products:
        (managed if p.is_stock_managed else unmanaged).append(p)

    context = {
        "stall": stall,
        "managed_products": managed,
        "unmanaged_products": unmanaged,
        "active_tab": "stock",
    }
    return render(request, "core/stock.html", context)


def _handle_stock_post(request, stall, access_token):
    action = request.POST.get("action", "")
    product_id = request.POST.get("product_id", "")
    value = request.POST.get("value", "")

    # この屋台に属する有効な商品だけを対象にする（他屋台の商品IDを弾く）
    product = get_object_or_404(
        stall.products, pk=_safe_int(product_id), is_active=True
    )

    try:
        amount = int(value)
    except (TypeError, ValueError):
        messages.error(request, "数値の形式が不正です。")
        return redirect("core:stock_manage", access_token=access_token)

    if action == "restock":
        if amount <= 0:
            messages.error(request, "補充数は1以上で入力してください。")
            return redirect("core:stock_manage", access_token=access_token)
        try:
            record_stock_adjustment(
                product, amount, kind="restock", note=""
            )
        except ValidationError as e:
            messages.error(request, "；".join(e.messages))
            return redirect("core:stock_manage", access_token=access_token)
        messages.success(request, f"{product.name} を {amount} 補充しました")

    elif action == "stocktake":
        if amount < 0:
            messages.error(request, "棚卸数は0以上で入力してください。")
            return redirect("core:stock_manage", access_token=access_token)
        # 現在残数を最新の状態で取り直し、差分を delta として記録する
        current = product.remaining_stock or 0
        delta = amount - current
        if delta == 0:
            messages.success(request, f"{product.name} は変更ありません")
            return redirect("core:stock_manage", access_token=access_token)
        try:
            record_stock_adjustment(
                product, delta, kind="stocktake", note=""
            )
        except ValidationError as e:
            messages.error(request, "；".join(e.messages))
            return redirect("core:stock_manage", access_token=access_token)
        messages.success(request, f"{product.name} を {amount} に修正しました")

    elif action == "set_initial":
        # 開始個数の設定は在庫管理対象かつ売上ゼロの商品のみ許可する。
        # UI で入力を無効化していても、サーバ側で必ず弾く（改ざん対策）。
        if not product.is_stock_managed:
            messages.error(request, "この商品は在庫管理の対象外です。")
            return redirect("core:stock_manage", access_token=access_token)
        if product.sold_quantity > 0:
            messages.error(
                request,
                f"{product.name} は売上があるため開始個数を変更できません。",
            )
            return redirect("core:stock_manage", access_token=access_token)
        if amount < 0:
            messages.error(request, "開始個数は0以上で入力してください。")
            return redirect("core:stock_manage", access_token=access_token)
        product.initial_stock = amount
        product.save(update_fields=["initial_stock"])
        messages.success(
            request, f"{product.name} の開始個数を {amount} に設定しました"
        )

    else:
        messages.error(request, "操作の指定が不正です。")

    return redirect("core:stock_manage", access_token=access_token)


def _safe_int(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


# ===== 履歴・取消 =====

def history(request, access_token):
    """直近の記録一覧（売上・経費・在庫調整）から選んで取消する画面。"""
    stall = _get_stall(access_token)
    if request.method == "POST":
        return _handle_history_post(request, stall, access_token)

    # 直近N件（各20件）を新しい順に。3種類をまとめて時系列表示するため
    # それぞれにkindタグを付けて結合し、created_atで降順ソートする。
    sales = [{"kind": "sale", "obj": s, "created_at": s.created_at} for s in
             stall.sales.order_by("-created_at")[:20]]
    expenses = [{"kind": "expense", "obj": e, "created_at": e.created_at} for e in
                stall.expenses.order_by("-created_at")[:20]]
    adjustments = [{"kind": "stock", "obj": a, "created_at": a.created_at} for a in
                   StockAdjustment.objects.filter(product__stall=stall).select_related("product").order_by("-created_at")[:20]]
    entries = sorted(sales + expenses + adjustments, key=lambda e: e["created_at"], reverse=True)[:30]

    context = {"stall": stall, "entries": entries, "active_tab": "history"}
    return render(request, "core/history.html", context)


def _handle_history_post(request, stall, access_token):
    kind = request.POST.get("kind", "")
    obj_id = _safe_int(request.POST.get("id", ""))

    if kind == "sale":
        sale = get_object_or_404(stall.sales, pk=obj_id)
        label = f"¥{sale.total:,.0f} の売上"
        void_sale(sale)
    elif kind == "expense":
        expense = get_object_or_404(stall.expenses, pk=obj_id)
        label = f"¥{expense.amount:,.0f} の経費"
        void_expense(expense)
    elif kind == "stock":
        adjustment = get_object_or_404(
            StockAdjustment.objects.filter(product__stall=stall), pk=obj_id
        )
        label = f"{adjustment.product.name} の在庫調整"
        void_stock_adjustment(adjustment)
    else:
        messages.error(request, "操作の指定が不正です。")
        return redirect("core:history", access_token=access_token)

    messages.success(request, f"{label}を取り消しました")
    return redirect("core:history", access_token=access_token)


# ===== ヘルスチェック（監視サービス用・トークン不要） =====

def healthz(request):
    """スリープ対策の軽量ヘルスチェック。認証不要・DBアクセスなし。"""
    return HttpResponse("ok")


def practice(request):
    """操作練習用のデモページ（認証不要・DBアクセスなし）。

    メンバーがスマホで操作に慣れられるよう、本番と同じ画面構成のデモを提供する。
    入力はすべてブラウザ内だけで完結し、DB には一切保存しない（再読み込みで消える）。
    """
    return render(request, "core/practice.html")


# ===== ダッシュボード（イベント全体） =====

def dashboard(request, dashboard_token):
    """イベント全体の集計ダッシュボード（閲覧専用・トークンアクセス制御）。"""
    event = get_object_or_404(Event, dashboard_token=dashboard_token)
    today = timezone.localdate()

    sales = Sale.objects.filter(stall__event=event)
    expenses = Expense.objects.filter(stall__event=event)

    total_sales = sales.aggregate(s=Sum("total"))["s"] or Decimal("0")
    today_agg = sales.filter(created_at__date=today).aggregate(
        s=Sum("total"), c=Count("id")
    )
    today_sales = today_agg["s"] or Decimal("0")
    today_count = today_agg["c"] or 0

    total_expenses = expenses.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    net_profit = total_sales - total_expenses

    # 屋台別売上・現金残高
    stalls = list(event.stalls.filter(is_active=True))
    stall_sales = {
        row["stall"]: row["s"]
        for row in sales.values("stall").annotate(s=Sum("total"))
    }
    stall_rows = []
    cash_total = Decimal("0")
    for st in stalls:
        st_sales = stall_sales.get(st.id, Decimal("0")) or Decimal("0")
        cash = st.cash_balance
        cash_total += cash
        stall_rows.append({
            "stall": st,
            "sales": st_sales,
            "cash": cash,
            "color": st.theme_color or "#2E3A66",
        })
    max_sales = max((r["sales"] for r in stall_rows), default=Decimal("0"))
    for r in stall_rows:
        r["pct"] = (
            int(r["sales"] * 100 / max_sales) if max_sales else 0
        )

    # 収支バー（純利益を売上比で表示）
    expense_pct = (
        int(total_expenses * 100 / total_sales) if total_sales else 0
    )

    # 在庫アラート（残少・売切）
    alerts = []
    for st in stalls:
        for p in st.products.filter(is_active=True, is_stock_managed=True):
            remaining = p.remaining_stock
            if remaining is None:
                continue
            if remaining <= 0:
                alerts.append({"product": p, "stall": st, "remaining": remaining, "out": True})
            elif remaining <= p.low_stock_threshold:
                alerts.append({"product": p, "stall": st, "remaining": remaining, "out": False})
    # 売切→残少の順で表示
    alerts.sort(key=lambda a: (not a["out"], a["remaining"]))

    context = {
        "event": event,
        "today": today,
        "total_sales": total_sales,
        "today_sales": today_sales,
        "today_count": today_count,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "stall_rows": stall_rows,
        "cash_total": cash_total,
        "expense_pct": expense_pct,
        "alerts": alerts,
    }
    return render(request, "core/dashboard.html", context)
