"""core アプリの URL ルーティング。

屋台売上入力: /s/<access_token>/
屋台経費入力: /s/<access_token>/expense/
屋台在庫管理: /s/<access_token>/stock/
全体ダッシュボード: /dashboard/<dashboard_token>/
"""

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("s/<str:access_token>/", views.sale_input, name="sale_input"),
    path("s/<str:access_token>/expense/", views.expense_input, name="expense_input"),
    path("s/<str:access_token>/stock/", views.stock_manage, name="stock_manage"),
    path("dashboard/<str:dashboard_token>/", views.dashboard, name="dashboard"),
]
