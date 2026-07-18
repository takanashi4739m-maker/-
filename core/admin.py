from django.contrib import admin

from .models import (
    CashEntry,
    Event,
    Expense,
    Product,
    Sale,
    SaleItem,
    Stall,
    StockAdjustment,
)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    # 運用者がダッシュボードURL用のトークンを一覧で確認できるよう表示する。
    list_display = ("name", "dashboard_token", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
    # トークンは自動生成の機密値。誤編集防止のため読み取り専用で表示。
    readonly_fields = ("dashboard_token", "created_at")


@admin.register(Stall)
class StallAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "event",
        "access_token",
        "initial_cash",
        "cash_balance",
        "display_order",
        "is_active",
    )
    list_filter = ("event", "is_active")
    search_fields = ("name",)
    readonly_fields = ("access_token", "cash_balance", "created_at")

    @admin.display(description="現金残高")
    def cash_balance(self, obj):
        return obj.cash_balance


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "stall",
        "price",
        "is_stock_managed",
        "initial_stock",
        "remaining_stock",
        "is_low_stock",
        "is_active",
    )
    list_filter = ("stall", "is_stock_managed", "is_active")
    search_fields = ("name",)

    @admin.display(description="残在庫")
    def remaining_stock(self, obj):
        return obj.remaining_stock

    @admin.display(boolean=True, description="在庫僅少")
    def is_low_stock(self, obj):
        return obj.is_low_stock


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 1
    # unit_price は空欄なら save() で現在価格を自動スナップショット。
    fields = ("product", "quantity", "unit_price")


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("__str__", "stall", "total", "created_at")
    list_filter = ("stall",)
    readonly_fields = ("total", "created_at")
    inlines = [SaleItemInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # 明細保存後に合計キャッシュを再計算。
        form.instance.recalc_total()


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("__str__", "stall", "amount", "category", "paid_in_cash", "created_at")
    list_filter = ("stall", "category", "paid_in_cash")
    search_fields = ("memo",)
    readonly_fields = ("created_at",)


@admin.register(CashEntry)
class CashEntryAdmin(admin.ModelAdmin):
    list_display = ("__str__", "stall", "entry_type", "amount", "created_at")
    list_filter = ("stall", "entry_type")
    search_fields = ("memo",)
    readonly_fields = ("created_at",)


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("__str__", "product", "delta", "kind", "created_at")
    list_filter = ("kind", "product__stall")
    search_fields = ("product__name", "note")
    readonly_fields = ("created_at",)
