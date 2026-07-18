"""各屋台のアクセスURLとイベントのダッシュボードURLを一覧表示する管理コマンド。

本番DBは migrate で初期データが入るが、access_token / dashboard_token は
ランダム生成のため本番固有になる。運用者がURLを確認できるようにする。

使い方:
    python manage.py show_urls
    BASE_URL=https://<app>.onrender.com python manage.py show_urls  # フルURL表示

BASE_URL 環境変数（または --base-url）があればフルURLで、無ければパスのみ表示する。
"""

import os
import sys

from django.core.management.base import BaseCommand
from django.urls import reverse

from core.models import Event, Stall


class Command(BaseCommand):
    help = "各屋台のアクセスURLとイベントのダッシュボードURLを一覧表示する。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url",
            dest="base_url",
            default=None,
            help="フルURL表示に使うベースURL（未指定時は環境変数 BASE_URL）。",
        )

    def _url(self, base_url: str, path: str) -> str:
        if base_url:
            return f"{base_url.rstrip('/')}{path}"
        return path

    def handle(self, *args, **options):
        # 屋台名の絵文字（🍺 等）を Windows の cp932 コンソールでも落とさないよう、
        # 出力を UTF-8 に再設定する（Linux/本番は既定で UTF-8 のため無害）。
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass

        base_url = options.get("base_url") or os.environ.get("BASE_URL", "")

        events = Event.objects.prefetch_related("stalls").order_by("id")
        if not events:
            self.stdout.write(self.style.WARNING("Event が登録されていません。"))
            return

        for event in events:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(f"■ イベント: {event.name}"))

            dashboard_path = reverse(
                "core:dashboard",
                kwargs={"dashboard_token": event.dashboard_token},
            )
            self.stdout.write(
                f"  ダッシュボード: {self._url(base_url, dashboard_path)}"
            )

            stalls = event.stalls.all().order_by("display_order", "id")
            if not stalls:
                self.stdout.write("  （屋台がありません）")
                continue

            self.stdout.write("  屋台:")
            for stall in stalls:
                sale_path = reverse(
                    "core:sale_input",
                    kwargs={"access_token": stall.access_token},
                )
                active = "" if stall.is_active else "（無効）"
                label = f"{stall.emoji}{stall.name}{active}"
                self.stdout.write(
                    f"    - {label}: {self._url(base_url, sale_path)}"
                )

        self.stdout.write("")
        if not base_url:
            self.stdout.write(
                self.style.NOTICE(
                    "※ フルURL表示には BASE_URL 環境変数か --base-url を指定してください。"
                )
            )
