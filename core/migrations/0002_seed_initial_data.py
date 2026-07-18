"""初期データ投入（Event 1 / Stall 3 / 各商品）。

金額はすべて Decimal（整数円）。射的商品は is_stock_managed=False。
reverse では投入したイベントを削除する（PROTECTのため子から順に削除）。
"""

from decimal import Decimal

from django.db import migrations


EVENT_NAME = "夏祭り2026"


def seed(apps, schema_editor):
    Event = apps.get_model("core", "Event")
    Stall = apps.get_model("core", "Stall")
    Product = apps.get_model("core", "Product")

    event = Event.objects.create(name=EVENT_NAME, is_active=True)

    # 屋台（display_order順）
    drink = Stall.objects.create(
        event=event, name="ドリンク", emoji="🍺",
        initial_cash=Decimal("20000"), display_order=1,
    )
    shooting = Stall.objects.create(
        event=event, name="射的", emoji="🎯",
        initial_cash=Decimal("10000"), display_order=2,
    )
    snack = Stall.objects.create(
        event=event, name="軽食", emoji="🍘",
        initial_cash=Decimal("15000"), display_order=3,
    )

    # ドリンク（在庫管理あり）
    drink_products = [
        ("生ビール", "400", 100),
        ("酎ハイ", "300", 100),
        ("ハイボール", "300", 100),
        ("缶ジュース¥100", "100", 80),
        ("缶ジュース¥120", "120", 80),
        ("缶ジュース¥150", "150", 80),
    ]
    for order, (name, price, stock) in enumerate(drink_products, start=1):
        Product.objects.create(
            stall=drink, name=name, price=Decimal(price),
            is_stock_managed=True, initial_stock=stock, display_order=order,
        )

    # 射的（在庫管理なし）
    shooting_products = [
        ("射的 3発", "200"),
        ("射的 5発", "300"),
    ]
    for order, (name, price) in enumerate(shooting_products, start=1):
        Product.objects.create(
            stall=shooting, name=name, price=Decimal(price),
            is_stock_managed=False, initial_stock=None, display_order=order,
        )

    # 軽食（在庫管理あり）
    Product.objects.create(
        stall=snack, name="ソースせんべい", price=Decimal("150"),
        is_stock_managed=True, initial_stock=120, display_order=1,
    )


def unseed(apps, schema_editor):
    Event = apps.get_model("core", "Event")
    Stall = apps.get_model("core", "Stall")
    Product = apps.get_model("core", "Product")

    event = Event.objects.filter(name=EVENT_NAME).first()
    if event is None:
        return
    Product.objects.filter(stall__event=event).delete()
    Stall.objects.filter(event=event).delete()
    event.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
