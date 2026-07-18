"""core アプリのビジネスロジック自動テスト。

本番投入前の品質担保が目的。モデル計算・サービス層（トランザクション/検証）・
ビュー（トークンアクセス制御・PRG・集計）を網羅する。

注意:
- 金額はすべて Decimal（整数円）。float 混入がないことも検証する。
- テストDBにはマイグレーション 0002/0004 で初期データ
  （Event「夏祭り2026」/屋台3/商品9/theme_color）が投入される。
  件数系のアサートは初期データ込み、または前後差分で検証する。
- 各テストは TestCase により自動ロールバックされ、開発用 db.sqlite3 や
  初期データには影響しない。
- テンプレートは本番設定だと ManifestStaticFilesStorage を使うため、
  ビューのGETレンダリング時に staticfiles マニフェストを要求してしまう。
  テストでは STORAGES を通常の StaticFilesStorage に上書きして回避する
  （プロダクトコードは変更しない）。
"""

from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import (
    CashEntry,
    Event,
    Expense,
    Product,
    Sale,
    SaleItem,
    Stall,
    StockAdjustment,
    generate_token,
)
from .services import (
    ExpenseValidationError,
    SaleValidationError,
    StockValidationError,
    record_expense,
    record_sale,
    record_stock_adjustment,
)

# ビュー描画テスト用: マニフェストを要求しない通常の静的ストレージへ差し替える。
TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}


class BaseFixtureMixin:
    """各テストで使う独立したイベント/屋台/商品を用意する。

    初期データ（夏祭り2026）とは別に自分のオブジェクトを作り、
    テストを決定的にする。件数は原則として前後差分で検証する。
    """

    def make_fixtures(self):
        self.event = Event.objects.create(name="テスト祭り")
        self.stall = Stall.objects.create(
            event=self.event,
            name="テスト屋台",
            emoji="🍺",
            initial_cash=Decimal("10000"),
            display_order=1,
        )
        # 在庫管理対象の商品（初期在庫100, 閾値5）
        self.drink = Product.objects.create(
            stall=self.stall,
            name="生ビール",
            price=Decimal("400"),
            is_stock_managed=True,
            initial_stock=100,
            low_stock_threshold=5,
        )
        # 在庫管理対象外の商品（射的相当）
        self.shooting = Product.objects.create(
            stall=self.stall,
            name="射的 3発",
            price=Decimal("200"),
            is_stock_managed=False,
            initial_stock=None,
        )
        # 別屋台（他屋台の商品混入テスト用）
        self.other_stall = Stall.objects.create(
            event=self.event,
            name="別屋台",
            initial_cash=Decimal("5000"),
            display_order=2,
        )
        self.other_product = Product.objects.create(
            stall=self.other_stall,
            name="よそのラムネ",
            price=Decimal("120"),
            is_stock_managed=True,
            initial_stock=50,
        )


# ============================================================
# 1. モデル計算
# ============================================================

class TokenGenerationTest(TestCase):
    """トークンが推測困難かつユニークに生成されること。"""

    def test_generate_token_unique(self):
        tokens = {generate_token() for _ in range(200)}
        self.assertEqual(len(tokens), 200)  # 重複なし

    def test_stall_and_event_tokens_are_unique_and_nonempty(self):
        event = Event.objects.create(name="祭A")
        s1 = Stall.objects.create(event=event, name="A", initial_cash=Decimal("0"))
        s2 = Stall.objects.create(event=event, name="B", initial_cash=Decimal("0"))
        self.assertTrue(s1.access_token)
        self.assertTrue(s2.access_token)
        self.assertNotEqual(s1.access_token, s2.access_token)
        self.assertTrue(event.dashboard_token)
        # 初期データのイベントとも衝突しないこと
        self.assertEqual(
            Event.objects.filter(dashboard_token=event.dashboard_token).count(), 1
        )


class ProductStockTest(BaseFixtureMixin, TestCase):
    """在庫残数・在庫アラート・在庫管理対象外の挙動。"""

    def setUp(self):
        self.make_fixtures()

    def test_remaining_stock_with_sale_restock_stocktake(self):
        # 初期100
        self.assertEqual(self.drink.remaining_stock, 100)

        # 売上で 10 販売 → 90
        record_sale(self.stall, [{"id": self.drink.id, "qty": 10}])
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.remaining_stock, 90)
        self.assertEqual(self.drink.sold_quantity, 10)

        # 補充 +20 → 110
        record_stock_adjustment(self.drink, 20, kind="restock")
        self.assertEqual(self.drink.remaining_stock, 110)

        # 棚卸で目標100へ補正（delta = 100 - 110 = -10）→ 100
        record_stock_adjustment(self.drink, -10, kind="stocktake")
        self.assertEqual(self.drink.remaining_stock, 100)
        # Σ(delta) = +20 - 10 = +10
        self.assertEqual(self.drink.adjustment_total, 10)
        # 残数 = 100(初期) - 10(販売) + 10(調整) = 100
        self.assertEqual(
            self.drink.remaining_stock,
            self.drink.initial_stock - self.drink.sold_quantity
            + self.drink.adjustment_total,
        )

    def test_is_low_stock_threshold(self):
        self.assertFalse(self.drink.is_low_stock)  # 100 > 5
        # 96個販売 → 残4 <= 閾値5 → アラート
        record_sale(self.stall, [{"id": self.drink.id, "qty": 96}])
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.remaining_stock, 4)
        self.assertTrue(self.drink.is_low_stock)
        # ちょうど閾値（5）でもアラート（<=）
        record_stock_adjustment(self.drink, 1, kind="restock")  # 残5
        self.assertEqual(self.drink.remaining_stock, 5)
        self.assertTrue(self.drink.is_low_stock)
        # 閾値超（6）ならアラート解除
        record_stock_adjustment(self.drink, 1, kind="restock")  # 残6
        self.assertEqual(self.drink.remaining_stock, 6)
        self.assertFalse(self.drink.is_low_stock)

    def test_unmanaged_product_has_no_stock(self):
        self.assertFalse(self.shooting.is_stock_managed)
        self.assertIsNone(self.shooting.remaining_stock)
        self.assertFalse(self.shooting.is_low_stock)


class StallCashBalanceTest(BaseFixtureMixin, TestCase):
    """現金残高 = 初期釣り銭 + Σ(CashEntry.amount 符号付き)。"""

    def setUp(self):
        self.make_fixtures()

    def test_cash_balance_initial(self):
        self.assertEqual(self.stall.cash_balance, Decimal("10000"))
        self.assertIsInstance(self.stall.cash_balance, Decimal)

    def test_cash_balance_sum_signed_entries(self):
        CashEntry.objects.create(
            stall=self.stall,
            entry_type=CashEntry.EntryType.SALE,
            amount=Decimal("3000"),
        )
        CashEntry.objects.create(
            stall=self.stall,
            entry_type=CashEntry.EntryType.EXPENSE,
            amount=Decimal("-1200"),
        )
        CashEntry.objects.create(
            stall=self.stall,
            entry_type=CashEntry.EntryType.ADJUST,
            amount=Decimal("500"),
        )
        # 10000 + 3000 - 1200 + 500 = 12300
        self.assertEqual(self.stall.cash_balance, Decimal("12300"))


class SaleCalcTest(BaseFixtureMixin, TestCase):
    """Sale.recalc_total / SaleItem.subtotal / unit_price スナップショット。"""

    def setUp(self):
        self.make_fixtures()

    def test_subtotal_and_recalc_total(self):
        sale = Sale.objects.create(stall=self.stall)
        SaleItem.objects.create(sale=sale, product=self.drink, quantity=3)  # 400x3
        SaleItem.objects.create(sale=sale, product=self.shooting, quantity=2)  # 200x2
        item1 = sale.items.get(product=self.drink)
        self.assertEqual(item1.subtotal, Decimal("1200"))
        total = sale.recalc_total(save=True)
        # 1200 + 400 = 1600
        self.assertEqual(total, Decimal("1600"))
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal("1600"))

    def test_unit_price_snapshot_is_frozen(self):
        sale = record_sale(self.stall, [{"id": self.drink.id, "qty": 2}])
        item = sale.items.get()
        self.assertEqual(item.unit_price, Decimal("400"))  # 記録時点の価格
        # 後から商品価格を変更しても過去の明細は不変
        self.drink.price = Decimal("500")
        self.drink.save()
        item.refresh_from_db()
        self.assertEqual(item.unit_price, Decimal("400"))
        self.assertEqual(item.subtotal, Decimal("800"))
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal("800"))


class DecimalFieldTest(BaseFixtureMixin, TestCase):
    """金額系フィールドが Decimal かつ整数円であること（float混入なし）。"""

    def setUp(self):
        self.make_fixtures()

    def _assert_integer_decimal(self, value):
        self.assertIsInstance(value, Decimal)
        self.assertEqual(value, value.to_integral_value())  # 整数円

    def test_money_fields_are_integer_decimal(self):
        sale = record_sale(self.stall, [{"id": self.drink.id, "qty": 2}])
        expense = record_expense(
            self.stall, "1500", Expense.Category.PURCHASE, paid_in_cash=True
        )
        sale.refresh_from_db()
        expense.refresh_from_db()
        self.stall.refresh_from_db()
        self.drink.refresh_from_db()

        self._assert_integer_decimal(sale.total)
        self._assert_integer_decimal(sale.items.get().unit_price)
        self._assert_integer_decimal(sale.items.get().subtotal)
        self._assert_integer_decimal(expense.amount)
        self._assert_integer_decimal(self.drink.price)
        self._assert_integer_decimal(self.stall.initial_cash)
        self._assert_integer_decimal(self.stall.cash_balance)
        cash_entry = CashEntry.objects.get(sale=sale)
        self._assert_integer_decimal(cash_entry.amount)


# ============================================================
# 2. サービス（トランザクション・検証）
# ============================================================

class RecordSaleServiceTest(BaseFixtureMixin, TestCase):
    def setUp(self):
        self.make_fixtures()

    def test_record_sale_creates_sale_items_and_cash(self):
        sale = record_sale(
            self.stall,
            [
                {"id": self.drink.id, "qty": 2},      # 400x2 = 800
                {"id": self.shooting.id, "qty": 3},   # 200x3 = 600
            ],
        )
        self.assertEqual(sale.total, Decimal("1400"))
        self.assertEqual(sale.items.count(), 2)
        # 現金反映
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.cash_balance, Decimal("10000") + Decimal("1400"))
        entry = CashEntry.objects.get(sale=sale)
        self.assertEqual(entry.entry_type, CashEntry.EntryType.SALE)
        self.assertEqual(entry.amount, Decimal("1400"))
        # 在庫反映（管理対象のみ）
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.remaining_stock, 98)

    def test_record_sale_rejects_zero_quantity(self):
        before = Sale.objects.count()
        with self.assertRaises(SaleValidationError):
            record_sale(self.stall, [{"id": self.drink.id, "qty": 0}])
        self.assertEqual(Sale.objects.count(), before)  # ロールバック
        self.assertFalse(CashEntry.objects.filter(stall=self.stall).exists())

    def test_record_sale_rejects_negative_quantity(self):
        with self.assertRaises(SaleValidationError):
            record_sale(self.stall, [{"id": self.drink.id, "qty": -3}])

    def test_record_sale_rejects_empty(self):
        with self.assertRaises(SaleValidationError):
            record_sale(self.stall, [])

    def test_record_sale_rejects_other_stall_product(self):
        sale_before = Sale.objects.count()
        item_before = SaleItem.objects.count()
        cash_before = CashEntry.objects.filter(stall=self.stall).count()
        with self.assertRaises(SaleValidationError):
            record_sale(self.stall, [{"id": self.other_product.id, "qty": 1}])
        # 何も作られない（中途半端なデータが残らない = ロールバック）
        self.assertEqual(Sale.objects.count(), sale_before)
        self.assertEqual(SaleItem.objects.count(), item_before)
        self.assertEqual(
            CashEntry.objects.filter(stall=self.stall).count(), cash_before
        )

    def test_record_sale_rollback_when_one_item_invalid(self):
        """有効商品と他屋台商品を混ぜても、全体が拒否されロールバックされる。"""
        sale_before = Sale.objects.count()
        with self.assertRaises(SaleValidationError):
            record_sale(
                self.stall,
                [
                    {"id": self.drink.id, "qty": 2},          # 有効
                    {"id": self.other_product.id, "qty": 1},  # 他屋台 → 全体拒否
                ],
            )
        self.assertEqual(Sale.objects.count(), sale_before)
        self.assertFalse(CashEntry.objects.filter(stall=self.stall).exists())


class RecordExpenseServiceTest(BaseFixtureMixin, TestCase):
    def setUp(self):
        self.make_fixtures()

    def test_expense_paid_in_cash_reduces_balance(self):
        expense = record_expense(
            self.stall, "1500", Expense.Category.PURCHASE, paid_in_cash=True
        )
        self.assertEqual(expense.amount, Decimal("1500"))
        entry = CashEntry.objects.get(expense=expense)
        self.assertEqual(entry.amount, Decimal("-1500"))  # 出金は負
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.cash_balance, Decimal("8500"))

    def test_expense_not_cash_does_not_touch_balance(self):
        expense = record_expense(
            self.stall, "2000", Expense.Category.SUPPLY, paid_in_cash=False
        )
        self.assertFalse(CashEntry.objects.filter(expense=expense).exists())
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.cash_balance, Decimal("10000"))  # 不変

    def test_expense_rejects_negative(self):
        with self.assertRaises(ExpenseValidationError):
            record_expense(self.stall, "-100", Expense.Category.PURCHASE)

    def test_expense_rejects_zero(self):
        with self.assertRaises(ExpenseValidationError):
            record_expense(self.stall, "0", Expense.Category.PURCHASE)

    def test_expense_rejects_float(self):
        # float は精度不安のため拒否
        with self.assertRaises(ExpenseValidationError):
            record_expense(self.stall, 100.5, Expense.Category.PURCHASE)

    def test_expense_rejects_non_integer_amount(self):
        with self.assertRaises(ExpenseValidationError):
            record_expense(self.stall, "100.5", Expense.Category.PURCHASE)

    def test_expense_rejects_invalid_category(self):
        before = Expense.objects.count()
        with self.assertRaises(ExpenseValidationError):
            record_expense(self.stall, "500", "not_a_category")
        self.assertEqual(Expense.objects.count(), before)  # ロールバック


class RecordStockAdjustmentServiceTest(BaseFixtureMixin, TestCase):
    def setUp(self):
        self.make_fixtures()

    def test_restock_increases_stock(self):
        adj = record_stock_adjustment(self.drink, 30, kind="restock")
        self.assertEqual(adj.delta, 30)
        self.assertEqual(self.drink.remaining_stock, 130)

    def test_stocktake_delta_to_target(self):
        # 20販売で残80 → 棚卸で実数75に補正（delta = 75 - 80 = -5）
        record_sale(self.stall, [{"id": self.drink.id, "qty": 20}])
        self.drink.refresh_from_db()
        current = self.drink.remaining_stock  # 80
        target = 75
        record_stock_adjustment(
            self.drink, target - current, kind="stocktake"
        )
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.remaining_stock, 75)

    def test_reject_unmanaged_product(self):
        before = StockAdjustment.objects.count()
        with self.assertRaises(StockValidationError):
            record_stock_adjustment(self.shooting, 10, kind="restock")
        self.assertEqual(StockAdjustment.objects.count(), before)

    def test_reject_zero_delta(self):
        with self.assertRaises(StockValidationError):
            record_stock_adjustment(self.drink, 0, kind="restock")

    def test_reject_invalid_kind(self):
        with self.assertRaises(StockValidationError):
            record_stock_adjustment(self.drink, 5, kind="bogus")


# ============================================================
# 3. ビュー・アクセス制御
# ============================================================

@override_settings(STORAGES=TEST_STORAGES)
class AccessControlTest(BaseFixtureMixin, TestCase):
    def setUp(self):
        self.make_fixtures()
        self.client = Client()

    def test_valid_token_get_200(self):
        for name in ("sale_input", "expense_input", "stock_manage"):
            url = reverse(f"core:{name}", args=[self.stall.access_token])
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, name)

    def test_invalid_token_404(self):
        for name in ("sale_input", "expense_input", "stock_manage"):
            url = reverse(f"core:{name}", args=["invalid-token-xxx"])
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 404, name)

    def test_dashboard_valid_and_invalid(self):
        ok = reverse("core:dashboard", args=[self.event.dashboard_token])
        self.assertEqual(self.client.get(ok).status_code, 200)
        ng = reverse("core:dashboard", args=["nope"])
        self.assertEqual(self.client.get(ng).status_code, 404)


@override_settings(STORAGES=TEST_STORAGES)
class SaleViewTest(BaseFixtureMixin, TestCase):
    def setUp(self):
        self.make_fixtures()
        self.client = Client()
        self.url = reverse("core:sale_input", args=[self.stall.access_token])

    def _messages(self, response):
        return [m.message for m in get_messages(response.wsgi_request)]

    def test_sale_post_creates_sale_and_redirects(self):
        import json

        before = Sale.objects.filter(stall=self.stall).count()
        resp = self.client.post(
            self.url,
            {"items": json.dumps([{"id": self.drink.id, "qty": 2}])},
        )
        self.assertEqual(resp.status_code, 302)  # PRG
        self.assertEqual(Sale.objects.filter(stall=self.stall).count(), before + 1)
        sale = Sale.objects.filter(stall=self.stall).latest("id")
        self.assertEqual(sale.total, Decimal("800"))
        msgs = self._messages(resp)
        self.assertTrue(any("記録しました" in m for m in msgs))

    def test_sale_post_other_stall_product_rejected(self):
        import json

        before = Sale.objects.filter(stall=self.stall).count()
        resp = self.client.post(
            self.url,
            {"items": json.dumps([{"id": self.other_product.id, "qty": 1}])},
        )
        self.assertEqual(resp.status_code, 302)
        # 売上件数不変（拒否）
        self.assertEqual(Sale.objects.filter(stall=self.stall).count(), before)
        msgs = self._messages(resp)
        self.assertTrue(any("扱っていない" in m for m in msgs))

    def test_sale_post_empty_rejected(self):
        before = Sale.objects.filter(stall=self.stall).count()
        resp = self.client.post(self.url, {"items": "[]"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Sale.objects.filter(stall=self.stall).count(), before)


@override_settings(STORAGES=TEST_STORAGES)
class ExpenseViewTest(BaseFixtureMixin, TestCase):
    def setUp(self):
        self.make_fixtures()
        self.client = Client()
        self.url = reverse("core:expense_input", args=[self.stall.access_token])

    def _messages(self, response):
        return [m.message for m in get_messages(response.wsgi_request)]

    def test_expense_post_cash_creates_and_reflects(self):
        resp = self.client.post(
            self.url,
            {"amount": "1500", "category": "purchase", "note": "氷", "paid_in_cash": "1"},
        )
        self.assertEqual(resp.status_code, 302)
        expense = Expense.objects.filter(stall=self.stall).latest("id")
        self.assertEqual(expense.amount, Decimal("1500"))
        self.assertTrue(expense.paid_in_cash)
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.cash_balance, Decimal("8500"))
        self.assertTrue(any("記録しました" in m for m in self._messages(resp)))

    def test_expense_post_non_cash_no_balance_change(self):
        resp = self.client.post(
            self.url,
            {"amount": "2000", "category": "supply", "paid_in_cash": "0"},
        )
        self.assertEqual(resp.status_code, 302)
        expense = Expense.objects.filter(stall=self.stall).latest("id")
        self.assertFalse(expense.paid_in_cash)
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.cash_balance, Decimal("10000"))

    def test_expense_post_invalid_amount_rejected(self):
        before = Expense.objects.filter(stall=self.stall).count()
        resp = self.client.post(
            self.url, {"amount": "-100", "category": "purchase"}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            Expense.objects.filter(stall=self.stall).count(), before
        )


@override_settings(STORAGES=TEST_STORAGES)
class StockViewTest(BaseFixtureMixin, TestCase):
    def setUp(self):
        self.make_fixtures()
        self.client = Client()
        self.url = reverse("core:stock_manage", args=[self.stall.access_token])

    def _messages(self, response):
        return [m.message for m in get_messages(response.wsgi_request)]

    def test_restock_post(self):
        resp = self.client.post(
            self.url,
            {"action": "restock", "product_id": self.drink.id, "value": "25"},
        )
        self.assertEqual(resp.status_code, 302)
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.remaining_stock, 125)
        self.assertTrue(
            StockAdjustment.objects.filter(
                product=self.drink, kind="restock", delta=25
            ).exists()
        )

    def test_stocktake_post_records_delta_to_target(self):
        # 先に10販売 → 残90。棚卸で実数70へ（delta = 70 - 90 = -20）
        record_sale(self.stall, [{"id": self.drink.id, "qty": 10}])
        resp = self.client.post(
            self.url,
            {"action": "stocktake", "product_id": self.drink.id, "value": "70"},
        )
        self.assertEqual(resp.status_code, 302)
        self.drink.refresh_from_db()
        self.assertEqual(self.drink.remaining_stock, 70)
        adj = StockAdjustment.objects.filter(
            product=self.drink, kind="stocktake"
        ).latest("id")
        self.assertEqual(adj.delta, -20)

    def test_stock_post_other_stall_product_404(self):
        resp = self.client.post(
            self.url,
            {"action": "restock", "product_id": self.other_product.id, "value": "5"},
        )
        # 他屋台の商品IDは stall.products から取得できず 404
        self.assertEqual(resp.status_code, 404)


@override_settings(STORAGES=TEST_STORAGES)
class DashboardAggregationTest(TestCase):
    """ダッシュボードの集計値が投入データに対して正しいこと。"""

    def setUp(self):
        self.client = Client()
        self.event = Event.objects.create(name="集計テスト祭")
        self.stall_a = Stall.objects.create(
            event=self.event, name="A", initial_cash=Decimal("10000"), display_order=1
        )
        self.stall_b = Stall.objects.create(
            event=self.event, name="B", initial_cash=Decimal("5000"), display_order=2
        )
        self.pa = Product.objects.create(
            stall=self.stall_a, name="ビール", price=Decimal("400"),
            is_stock_managed=True, initial_stock=10, low_stock_threshold=5,
        )
        self.pb = Product.objects.create(
            stall=self.stall_b, name="せんべい", price=Decimal("150"),
            is_stock_managed=True, initial_stock=3, low_stock_threshold=5,
        )
        # 売上: A で 400x3=1200, B で 150x2=300
        record_sale(self.stall_a, [{"id": self.pa.id, "qty": 3}])
        record_sale(self.stall_b, [{"id": self.pb.id, "qty": 2}])
        # 経費: A で現金1000（仕入れ）
        record_expense(self.stall_a, "1000", Expense.Category.PURCHASE, paid_in_cash=True)
        self.url = reverse("core:dashboard", args=[self.event.dashboard_token])

    def test_dashboard_aggregates(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context

        # 総売上 = 1200 + 300 = 1500
        self.assertEqual(ctx["total_sales"], Decimal("1500"))
        # 総経費 = 1000
        self.assertEqual(ctx["total_expenses"], Decimal("1000"))
        # 純利益 = 1500 - 1000 = 500
        self.assertEqual(ctx["net_profit"], Decimal("500"))

        # 屋台別現金: A = 10000 + 1200(売上) - 1000(経費) = 10200
        #             B = 5000 + 300 = 5300
        rows = {r["stall"].id: r for r in ctx["stall_rows"]}
        self.assertEqual(rows[self.stall_a.id]["cash"], Decimal("10200"))
        self.assertEqual(rows[self.stall_a.id]["sales"], Decimal("1200"))
        self.assertEqual(rows[self.stall_b.id]["cash"], Decimal("5300"))
        self.assertEqual(rows[self.stall_b.id]["sales"], Decimal("300"))
        # 現金合計
        self.assertEqual(ctx["cash_total"], Decimal("15500"))

    def test_dashboard_stock_alerts(self):
        # pb: 初期3 - 2販売 = 残1 → 残少アラート（<=5, >0）
        # pa: 初期10 - 3販売 = 残7 → アラート対象外
        resp = self.client.get(self.url)
        alerts = resp.context["alerts"]
        alerted = {a["product"].id for a in alerts}
        self.assertIn(self.pb.id, alerted)
        self.assertNotIn(self.pa.id, alerted)
        pb_alert = next(a for a in alerts if a["product"].id == self.pb.id)
        self.assertEqual(pb_alert["remaining"], 1)
        self.assertFalse(pb_alert["out"])  # 売切ではない
