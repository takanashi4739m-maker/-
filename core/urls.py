"""core アプリの URL ルーティング。

屋台売上入力: /s/<access_token>/
屋台経費入力: /s/<access_token>/expense/
屋台在庫管理: /s/<access_token>/stock/
屋台履歴・取消: /s/<access_token>/history/
全体ダッシュボード: /dashboard/<dashboard_token>/
ヘルスチェック（トークン不要）: /healthz/
"""

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("s/<str:access_token>/", views.sale_input, name="sale_input"),
    path("s/<str:access_token>/expense/", views.expense_input, name="expense_input"),
    path("s/<str:access_token>/stock/", views.stock_manage, name="stock_manage"),
    path("s/<str:access_token>/history/", views.history, name="history"),
    path("dashboard/<str:dashboard_token>/", views.dashboard, name="dashboard"),
    path("healthz/", views.healthz, name="healthz"),
    path("practice/", views.practice, name="practice"),
]
