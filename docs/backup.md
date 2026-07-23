# DBバックアップ手順（夏祭り屋台アプリ）

売上・経費・在庫などの帳簿データを守るため、Django標準の `dumpdata` コマンドで
定期的にバックアップを取る。新規コマンドの実装は行わず、既存の管理コマンドのみで運用する。

- バックアップ対象は `core` アプリのデータ（Event/Stall/Product/Sale/SaleItem/Expense/
  CashEntry/StockAdjustment）。Django自体の管理テーブル（セッション等）は対象外でよい。
- 出力形式はJSON（`--indent 2` で人間にも読める整形にする）。

---

## 1. ローカル開発DB（SQLite）のバックアップ

開発中に確認用データを壊したくない場合は、そのまま `db.sqlite3` をコピーしてもよいが、
DBエンジンに依存しない方法として `dumpdata` を推奨する。

```bash
python manage.py dumpdata core --indent 2 > backup_YYYYMMDD.json
```

復元する場合（既存データを一度クリアしてから流し込む想定）:

```bash
python manage.py flush --no-input   # 全データ削除（注意: 取り消し不可）
python manage.py loaddata backup_YYYYMMDD.json
```

---

## 2. 本番DB（Render / PostgreSQL）のバックアップ

本番は Render の無料プランを使用しており、**Web ServiceのShellが使えない**
（[docs/deploy-render.md](deploy-render.md) 参照）。そのため `manage.py dumpdata` を
本番サーバー上で直接実行することができない。

代わりに、**ローカルの開発環境から本番のPostgreSQLへ直接接続し、ローカルでdumpdataを実行する**。

### 2-1. 本番DBの「外部接続用URL」を確認する

1. Render ダッシュボード → PostgreSQL サービス（`festival-db`）を開く。
2. **Connections** セクションの **External Database URL**（外部接続用）をコピーする。
   - `render.yaml` の `DATABASE_URL`（Web Serviceに自動注入される接続文字列）は
     Render内部ネットワーク専用の**内部URL**であり、ローカルPCからは接続できない。
     必ず「External」と明記されたURLを使うこと。
   - 外部接続用URLも接続情報そのものが機密情報。第三者に共有しない・コミットしない。

### 2-2. ローカルから本番DBを参照してdumpdataを実行する

カレントの `.env` を上書きしないよう、コマンド実行時だけ環境変数を一時的に切り替える。

```bash
# 例（bash/Git Bash）。<外部接続URL> は 2-1 でコピーしたものに置き換える
DATABASE_URL="<外部接続URL>" python manage.py dumpdata core --indent 2 > backup_prod_YYYYMMDD.json
```

PowerShellの場合:

```powershell
$env:DATABASE_URL = "<外部接続URL>"
python manage.py dumpdata core --indent 2 > backup_prod_YYYYMMDD.json
Remove-Item Env:DATABASE_URL
```

- 実行前に `python manage.py migrate --check` 等で本番とローカルのマイグレーション状態が
  ズレていないか確認しておくと安全（ズレていると `dumpdata` 自体は動くが、後述の復元で
  問題が起きうる）。
- 生成された `backup_prod_YYYYMMDD.json` は帳簿データ（売上・経費・現金台帳）を含む機密ファイル。
  `.gitignore` 済みの場所（リポジトリ直下など）に置き、絶対にコミットしない。安全な場所
  （暗号化したクラウドストレージ等）に保管する。

### 2-3. 本番DBへ復元する（緊急時のみ）

`loaddata` は原則、本番投入前や障害復旧など限定的な場面でのみ使う。
実行前に必ず現在の本番データを別途 `dumpdata` でバックアップしておくこと。

```bash
DATABASE_URL="<外部接続URL>" python manage.py loaddata backup_prod_YYYYMMDD.json
```

---

## 3. 運用の目安

- 本番運用中（夏祭り当日）は、1日の営業終了後に必ず1回、上記2の手順でバックアップを取る。
- ファイル名に日付を含め（`backup_prod_YYYYMMDD.json`）、複数世代を残す。
- バックアップファイルは帳簿データそのものなので、取り扱いは元のDBアクセスと同等の注意を払う
  （関係者以外に渡さない・公開リポジトリに置かない）。
