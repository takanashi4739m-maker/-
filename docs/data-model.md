# 夏祭り屋台運営アプリ データモデル設計メモ

> 本ドキュメントは「設計案」です。実装（プロジェクト生成 / migrate）は未実施。
> 金額はすべて `Decimal`（`DecimalField`）で扱い、floatは使用しない方針。

---

## 1. 全体方針

- MVP優先（本番まで約1ヶ月）。まず「売上入力・経費・現金残高・在庫アラート」が回ることを最優先。
- 認証はログインなし。**推測されにくいトークン付きURL**でアクセス制御する。
- 屋台は3つ（🍺ドリンク / 🎯射的 / 🍘軽食）だが、モデル上はハードコードせず `Stall` レコードとして持つ（将来の増減・別イベント流用に強い）。
- **通貨は日本円（整数円）**。`DecimalField(decimal_places=0)` を採用。floatを避けつつ、円未満を持たない仕様を型で表現する。

### 金額の持ち方（重要な設計判断）

| 項目 | 方針 | 理由 |
|------|------|------|
| 単価 `unit_price` | `SaleItem` に**記録時点の価格をコピー保存** | 後で価格改定しても過去売上が変わらないようにする（Productのpriceは「現在の売値」、SaleItemのunit_priceは「その時売れた値」） |
| 小計 `subtotal` | `SaleItem` に保存（unit_price × quantity） | 再計算不要・監査しやすい。保存前に整合チェック |
| 合計 `total` | `Sale` に**キャッシュとして保存** | ダッシュボード集計を速く。明細から再計算できる冗長データ |
| 現金残高 | 都度計算せず **CashEntry台帳の積み上げ**で表現 | 監査可能・入出金の履歴が残る |

---

## 2. アプリ分割案

MVPは **単一アプリ `core`** に集約することを推奨。

理由:
- 規模が小さく（エンティティ7個程度）、少人数・短期。アプリ分割の管理コストが利得を上回る。
- Sale / Inventory / Cash は相互参照が多く、分割するとFKや循環importが煩雑になる。

```
festival/                 # プロジェクトルート（manage.py 直下）
├── manage.py
├── festival/             # 設定パッケージ
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
└── core/                 # 単一アプリ
    ├── models.py
    ├── views.py
    ├── urls.py
    ├── admin.py
    └── migrations/
```

将来分割する場合の目安: `core`（Event/Stall/Product基盤）/ `sales`（Sale/SaleItem）/ `ledger`（Expense/CashEntry）/ `dashboard`。ただしMVPでは行わない。

---

## 3. トークン付きURLでのアクセス制御

- `Stall.access_token`: 屋台ごとのユニークトークン。`/s/<token>/` で当該屋台の売上入力・経費・在庫画面へ。
- `Event.dashboard_token`: **全体ダッシュボード専用の別トークン**。`/dashboard/<token>/` で全屋台横断の集計を閲覧。屋台トークンとは分離し、運営責任者のみ共有。
- トークンは `secrets.token_urlsafe()` で生成し `unique=True`。URL露出前提なので十分な長さ（32バイト以上）にする。
- ビュー側では `get_object_or_404(Stall, access_token=token)` の形でトークン一致を必須化し、トークンを知らない者はアクセス不可とする（オブジェクトレベルのアクセス制御）。
- 補足: トークンURLは共有時に漏れうるため、本番では HTTPS 必須・アクセスログ記録・必要ならトークン再発行（`regenerate_token`）機能を後付けできるよう `access_token` は可変にしておく。

---

## 4. モデル定義案（models.py 相当）

```python
import secrets
from decimal import Decimal

from django.db import models
from django.core.validators import MinValueValidator


def generate_token() -> str:
    """URL用の推測困難なトークン。"""
    return secrets.token_urlsafe(32)


# 円通貨用の共通設定（floatは使わずDecimal / 小数点なし=整数円）
YEN_FIELD_KWARGS = dict(max_digits=12, decimal_places=0)


class Event(models.Model):
    """夏祭りイベント（屋台のグルーピング + 全体ダッシュボード用トークン）。"""
    name = models.CharField(max_length=100)
    dashboard_token = models.CharField(
        max_length=64, unique=True, default=generate_token,
        help_text="全体ダッシュボード閲覧用の推測困難トークン",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Stall(models.Model):
    """屋台。トークン付きURLでアクセスする単位。"""
    event = models.ForeignKey(
        Event, on_delete=models.PROTECT, related_name="stalls",
    )
    name = models.CharField(max_length=50)          # 例: ドリンク / 射的 / 軽食
    emoji = models.CharField(max_length=8, blank=True)  # 🍺 🎯 🍘
    access_token = models.CharField(
        max_length=64, unique=True, default=generate_token,
        help_text="この屋台の運営画面へアクセスするための推測困難トークン",
    )
    # 釣り銭準備金（開始時の手元現金）。現金残高計算の起点。
    initial_cash = models.DecimalField(
        default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))],
        **YEN_FIELD_KWARGS,
    )
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "id"]

    def __str__(self):
        return f"{self.emoji}{self.name}"


class Product(models.Model):
    """商品（SKU）。価格別に別レコード（缶ジュース¥100/¥120/¥150は別SKU）。"""
    stall = models.ForeignKey(
        Stall, on_delete=models.CASCADE, related_name="products",
    )
    name = models.CharField(max_length=100)   # 例: 生ビール / 射的 3発 / 缶ジュース¥120
    price = models.DecimalField(              # 「現在の売値」。過去売上には影響させない
        validators=[MinValueValidator(Decimal("0"))], **YEN_FIELD_KWARGS,
    )
    # 在庫管理の対象か（ドリンク・軽食=True、射的=False）
    is_stock_managed = models.BooleanField(default=True)
    initial_stock = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="在庫管理対象のみ。開始時の在庫数",
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=5, help_text="残数がこれ以下で在庫アラート",
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
    def remaining_stock(self):
        """残り在庫。管理対象外はNone。"""
        if not self.is_stock_managed or self.initial_stock is None:
            return None
        return self.initial_stock - self.sold_quantity

    @property
    def is_low_stock(self) -> bool:
        r = self.remaining_stock
        return r is not None and r <= self.low_stock_threshold


class Sale(models.Model):
    """売上=1会計（会計トレイ方式。複数商品・複数個数を含みうる）。"""
    stall = models.ForeignKey(
        Stall, on_delete=models.PROTECT, related_name="sales",
    )
    total = models.DecimalField(   # 明細合計のキャッシュ（集計高速化用）
        default=Decimal("0"), **YEN_FIELD_KWARGS,
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
        Sale, on_delete=models.CASCADE, related_name="items",
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="sale_items",
    )
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )
    # 記録時点の価格をコピー保存（Product.price を後で変えても過去は不変）
    unit_price = models.DecimalField(**YEN_FIELD_KWARGS)

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
        Stall, on_delete=models.PROTECT, related_name="expenses",
    )
    amount = models.DecimalField(
        validators=[MinValueValidator(Decimal("0"))], **YEN_FIELD_KWARGS,
    )
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.PURCHASE,
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
        Stall, on_delete=models.CASCADE, related_name="cash_entries",
    )
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    # 入金は正、出金は負のDecimal（符号で表現）
    amount = models.DecimalField(**YEN_FIELD_KWARGS)
    # 由来の紐付け（任意）。SET_NULL で元レコード削除時も台帳は残す
    sale = models.ForeignKey(
        Sale, null=True, blank=True, on_delete=models.SET_NULL,
    )
    expense = models.ForeignKey(
        Expense, null=True, blank=True, on_delete=models.SET_NULL,
    )
    memo = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "cash entries"

    def __str__(self):
        return f"{self.get_entry_type_display()} {self.amount:+}"
```

### 現金残高の算出（モデル外・サービス/集計）

```python
def stall_cash_balance(stall) -> Decimal:
    agg = stall.cash_entries.aggregate(s=models.Sum("amount"))
    return stall.initial_cash + (agg["s"] or Decimal("0"))
```

---

## 5. 設計上の補足・判断理由

- **在庫の持ち方**: MVPでは残数を保存せず `Product.remaining_stock`（initial_stock − 販売数）で都度計算。これで在庫アラートまで賄える。仕入れによる在庫補充・棚卸調整が必要になったら、後述の `StockAdjustment` を追加する（後回し可）。
- **現金 = 台帳方式**: 残高カラムを持つと更新競合・整合ずれのリスク。少人数同時入力でも、符号付き `CashEntry` の合算なら破綻しにくい。
- **削除方針**: 集計に効く親（Stall/Sale/Product）は基本 `PROTECT`。明細（SaleItem/CashEntry）は親に追随（CASCADE）。台帳の由来FKは `SET_NULL` で履歴を保全。
- **入力値検証**: 数量 `>=1`、金額 `>=0` を `validators` で強制。トークンは `unique`。ビュー層でトークン一致を必須化。
- **原子性**: 1会計の登録（Sale + 複数SaleItem + 現金CashEntry）は `transaction.atomic()` でまとめる（実装時）。

---

## 6. MVPスコープの切り分け

### 最初に作る（MVP必須）
- `Event` / `Stall`（トークン、初期釣り銭）
- `Product`（価格別SKU、在庫フラグ、初期在庫、アラート閾値）
- `Sale` / `SaleItem`（会計トレイ方式、単価スナップショット）
- `Expense`（金額・カテゴリ・メモ・現金支払フラグ）
- `CashEntry`（現金台帳）+ 残高算出
- ダッシュボード集計（売上・損益 = 売上 − 経費・現金残高・在庫アラート）は**集計クエリで実現**（専用モデル不要）

### 後回しでよい（MVP後）
- `StockAdjustment`（仕入れによる在庫補充・棚卸調整・在庫の増減履歴）
- トークン再発行 / アクセスログ / 監査ログ
- 商品カテゴリやトッピング等の複雑な商品構成
- 日次締め・レポート出力（CSV/PDF）
- ダッシュボード集計のキャッシュ用サマリーモデル（性能が問題になってから）

---

## 7. 想定エンティティ相関（サマリ）

```
Event 1──* Stall 1──* Product
                │           ▲
                │           │ (product, on_delete=PROTECT)
                ├──* Sale 1──* SaleItem
                ├──* Expense
                └──* CashEntry ─(任意FK)→ Sale / Expense
```
