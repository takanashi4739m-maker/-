"""既存屋台に theme_color（のれんの布色）を設定する data migration。

縁日パレットに沿って屋台ごとに色を割り当てる:
  ドリンク=藍 #2E3A66 / 射的=朱 #DD4B34 / 軽食=山吹 #C9871F

name で一致した屋台にのみ設定する（初期データを尊重）。
reverse では既定色（藍）に戻す。
"""

from django.db import migrations


STALL_COLORS = {
    "ドリンク": "#2E3A66",  # 藍
    "射的": "#DD4B34",      # 朱
    "軽食": "#C9871F",      # 山吹
}

DEFAULT_COLOR = "#2E3A66"


def set_colors(apps, schema_editor):
    Stall = apps.get_model("core", "Stall")
    for name, color in STALL_COLORS.items():
        Stall.objects.filter(name=name).update(theme_color=color)


def reset_colors(apps, schema_editor):
    Stall = apps.get_model("core", "Stall")
    for name in STALL_COLORS:
        Stall.objects.filter(name=name).update(theme_color=DEFAULT_COLOR)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_stall_theme_color"),
    ]

    operations = [
        migrations.RunPython(set_colors, reset_colors),
    ]
