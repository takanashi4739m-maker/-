import secrets
from decimal import Decimal

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models


def generate_token() -> str:
    """URL用の推測困難なトークン。"""
    return secrets.token_urlsafe(32)


# 円通貨用の共通設定（floatは使わずDecimal / 小数点なし=整数円）
YEN_FIELD_KWARGS = dict(max_digits=12, decimal_places=0)


class Event(models.Model):
    """夏祭りイベント（屋台のグルーピング + 全体ダッシュボード用トークン）。"""

    name = models.CharField(max_length=100)
    dashboard_token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_token,
        help_text="全体ダッシュボード閲覧用の推測困難トークン",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Stall(models.Model):
    """屋台。トークン付きURLでアクセスする単位。"""

    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="stalls",
    )
    name = models.CharField(max_length=50)  # 例: ドリンク / 射的 / 軽食
    emoji = models.CharField(max_length=8, blank=True)  # 🍺 🎯 🍘
    # のれん（暖簾）ラベル等に使うこの屋台のテーマ色。#RRGGBB の7文字。
    # 未指定（null/blank）なら藍をデフォルト表示に使う。
    theme_color = models.CharField(
        max_length=7,
        null=True,
        blank=True,
        default="#2E3A66",
        validators=[
            RegexValidator(
                r"^(#[0-9A-Fa-f]{6})?$",
                "色は #RRGGBB 形式（7文字）で指定してください。",
            )
        ],
        help_text="のれん等に使う屋台のテーマ色（#RRGGBB）。未指定は藍。",
    )
    access_token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_token,
        help_text="この屋台の運営画面へアクセスするための推測困難トークン",
    )
    # 釣り銭準備金（開始時の手元現金）。現金残高計算の起点。
    initial_cash = models.DecimalField(
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        **YEN_FIELD_KWARGS,
    )
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "id"]

    def __str__(self):
        return f"{self.emoji}{self.name}"

    @property
    def cash_balance(self) -> Decimal:
        """現金残高 = 初期釣り銭 + 台帳（CashEntry）の符号付き合計。"""
        agg = self.cash_entries.aggregate(s=models.Sum("amount"))
        return self.initial_cash + (agg["s"] or Decimal("0"))


class Product(models.Model):
    """商品（SKU）。価格別に別レコード（缶ジュース¥100/¥120/¥150は別SKU）。"""

    stall = models.ForeignKey(
        Stall,
        on_delete=models.CASCADE,
        related_name="products",
    )
    name = models.CharField(max_length=100)  # 例: 生ビール / 射的 3発 / 缶ジュース¥120
    price = models.DecimalField(  # 「現在の売値」。過去売上には影響させない
        validators=[MinValueValidator(Decimal("0"))],
        **YEN_FIELD_KWARGS,
    )
    # 在庫管理の対象か（ドリンク・軽食=True、射的=False）
    is_stock_managed = models.BooleanField(default=True)
    initial_stock = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="在庫管理対象のみ。開始時の在庫数",
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=5,
        help_text="残数がこれ以下で在庫アラート",
    )
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "id"]

    def __str__(self):
        return f"{self.name} (¥{self.price})"

    @property
    def sold_quantity(self) -> int:
        """販売済み総数（明細の数量合計）。"""
        agg = self.sale_items.aggregate(n=models.Sum("quantity"))
        return agg["n"] or 0

    @property
    def adjustment_total(self) -> int:
        """在庫調整（補充・棚卸）の符号付き合計。台帳=StockAdjustmentの積み上げ。"""
        agg = self.stock_adjustments.aggregate(s=models.Sum("delta"))
        return agg["s"] or 0

    @property
    def remaining_stock(self):
        """残り在庫。管理対象外はNone。

        残数 = 初期在庫 − 販売数 + Σ(在庫調整 delta)。
        補充（restock）は正のdelta、棚卸（stocktake）は差分をdeltaで記録する。
        """
        if not self.is_stock_managed or self.initial_stock is None:
            return None
        return self.initial_stock - self.sold_quantity + self.adjustment_total

    @property
    def is_low_stock(self) -> bool:
        r = self.remaining_stock
        return r is not None and r <= self.low_stock_threshold


class Sale(models.Model):
    """売上=1会計（会計トレイ方式。複数商品・複数個数を含みうる）。"""

    stall = models.ForeignKey(
        Stall,
        on_delete=models.PROTECT,
        related_name="sales",
    )
    total = models.DecimalField(  # 明細合計のキャッシュ（集計高速化用）
        default=Decimal("0"),
        **YEN_FIELD_KWARGS,
    )
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Sale#{self.pk} ¥{self.total}"

    def recalc_total(self, save=True):
        """明細から合計を再計算。"""
        agg = self.items.aggregate(
            t=models.Sum(models.F("unit_price") * models.F("quantity"))
        )
        self.total = agg["t"] or Decimal("0")
        if save:
            self.save(update_fields=["total"])
        return self.total


class SaleItem(models.Model):
    """売上明細=商品 × 数量 × 記録時点の単価。"""

    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="sale_items",
    )
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )
    # 記録時点の価格をコピー保存（Product.price を後で変えても過去は不変）。
    # null/blank許可は「空欄なら現在価格を自動スナップショット」を
    # フォーム/adminからも成立させるため（DBにNULLで残さない: save()で必ず補完）。
    unit_price = models.DecimalField(null=True, blank=True, **YEN_FIELD_KWARGS)

    @property
    def subtotal(self) -> Decimal:
        return self.unit_price * self.quantity

    def save(self, *args, **kwargs):
        # unit_price 未指定時は現在価格をスナップショット
        if self.unit_price is None and self.product_id:
            self.unit_price = self.product.price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"


class Expense(models.Model):
    """経費・仕入れ。"""

    class Category(models.TextChoices):
        PURCHASE = "purchase", "仕入れ"
        SUPPLY = "supply", "備品・消耗品"
        OTHER = "other", "その他"

    stall = models.ForeignKey(
        Stall,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    amount = models.DecimalField(
        validators=[MinValueValidator(Decimal("0"))],
        **YEN_FIELD_KWARGS,
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.PURCHASE,
    )
    memo = models.CharField(max_length=200, blank=True)
    # 現金で支払ったか（現金残高計算に反映するか）
    paid_in_cash = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_category_display()} ¥{self.amount}"


class CashEntry(models.Model):
    """屋台ごとの現金入出金台帳。残高は初期釣り銭 + entries の積み上げで算出。

    設計方針: 残高フィールドを持たず「台帳（元帳）」として記録することで、
    入出金の履歴と監査性を確保する。売上・経費の登録時に対応するCashEntryを
    サービス層（views/services）で作成する。
    """

    class EntryType(models.TextChoices):
        SALE = "sale", "売上（現金）"
        EXPENSE = "expense", "経費支払"
        ADJUST = "adjust", "手動調整"  # 釣り銭補充・両替・実査差異など

    stall = models.ForeignKey(
        Stall,
        on_delete=models.CASCADE,
        related_name="cash_entries",
    )
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    # 入金は正、出金は負のDecimal（符号で表現）
    amount = models.DecimalField(**YEN_FIELD_KWARGS)
    # 由来の紐付け（任意）。SET_NULL で元レコード削除時も台帳は残す
    sale = models.ForeignKey(
        Sale,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    expense = models.ForeignKey(
        Expense,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    memo = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "cash entries"

    def __str__(self):
        return f"{self.get_entry_type_display()} {self.amount:+}"


class StockAdjustment(models.Model):
    """在庫の手動増減台帳（仕入れ補充・棚卸調整）。

    設計方針: 残数フィールドを持たず「台帳（元帳）」として増減を積み上げる。
    Product.remaining_stock = 初期在庫 − 販売数 + Σ(delta) で残数を算出する。
    - restock（補充）: 仕入れで増えた数を正のdeltaで記録
    - stocktake（棚卸）: 実数への修正差分（目標数 − 現在数）をdeltaで記録（正負あり）
    """

    class Kind(models.TextChoices):
        RESTOCK = "restock", "仕入れ補充"
        STOCKTAKE = "stocktake", "棚卸調整"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="stock_adjustments",
    )
    # 符号付き増減数。補充は正、棚卸は差分（正負あり）
    delta = models.IntegerField(help_text="在庫の増減数（補充は正、棚卸は差分）")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product.name} {self.delta:+} ({self.get_kind_display()})"
