# Render デプロイ手順（夏祭り屋台アプリ）

本番は Render にデプロイする。DB は Render の PostgreSQL、静的ファイルは WhiteNoise が配信する。
開発は従来どおり SQLite（`DATABASE_URL` 未設定時に自動フォールバック）。

---

## 0. 前提

- 本リポジトリ直下に `render.yaml` / `Procfile` / `requirements.txt` / `.env.example` がある。
- `staticfiles/` と `.env` は `.gitignore` 済み（コミットしない）。

## 1. GitHub リポジトリを準備

1. GitHub に新規リポジトリを作成する。
2. ローカルからプッシュする。

   ```bash
   git init
   git add .
   git commit -m "本番化: Render/PostgreSQL/WhiteNoise 対応"
   git branch -M main
   git remote add origin https://github.com/<user>/<repo>.git
   git push -u origin main
   ```

   ※ `db.sqlite3`・`.env`・`staticfiles/` はコミットされない（`.gitignore` 済み）。

## 2. Render で Blueprint を適用

1. Render ダッシュボード → **New +** → **Blueprint** を選択。
2. 上記の GitHub リポジトリを連携する。
3. `render.yaml` が読み込まれ、以下が作成される。
   - Web Service `festival-yatai`（Python / gunicorn）
   - PostgreSQL `festival-db`
4. **Apply** を押す。

`render.yaml` により、以下が自動で行われる。

- Build: `pip install -r requirements.txt; python manage.py collectstatic --noinput; python manage.py migrate`
  （無料プランは preDeployCommand 非対応のため、migrate はビルド時に実行。初期データ 0002/0004 もここで投入）
- Start: `gunicorn config.wsgi:application`

## 3. 環境変数を設定

`render.yaml` で大半は自動設定されるが、`sync: false` の項目は初回に手入力する。

| 変数 | 設定値 | 備考 |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | （自動生成） | `generateValue: true` |
| `DJANGO_DEBUG` | `False` | 本番は必ず False |
| `DJANGO_ALLOWED_HOSTS` | （ホスト名を自動注入） | 独自ドメインは追記 |
| `DATABASE_URL` | （DBから自動リンク） | PostgreSQL 接続文字列 |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://festival-yatai.onrender.com` | ★手入力。実ホスト名に合わせる |
| `BASE_URL` | `https://festival-yatai.onrender.com` | ★手入力（任意）。show_urls のフルURL表示用 |
| `PYTHON_VERSION` | `3.12.10` | |

> デプロイ後に確定する実ホスト名（`https://<サービス名>.onrender.com`）を
> `DJANGO_CSRF_TRUSTED_ORIGINS` と `BASE_URL` に設定し、再デプロイする。
> これを設定しないと、トークンURLでの売上・経費・在庫の POST が CSRF で 403 になる。

## 4. 初回 migrate（初期データ投入）

Pre-Deploy コマンド（`python manage.py migrate`）で自動実行される。
初期データ（イベント「夏祭り2026」/ 屋台3 / 商品9 / テーマ色）もマイグレーションで投入される。

手動で流し直す場合は、Web Service の **Shell** から:

```bash
python manage.py migrate
```

## 5. アクセスURL（トークン）を確認

`access_token` / `dashboard_token` はDBごとにランダム生成されるため、本番では本番固有の値になる。
Web Service の **Shell** から次を実行し、各屋台と全体ダッシュボードのURLを確認する。

```bash
python manage.py show_urls
```

`BASE_URL` を設定していればフルURL（`https://.../s/<token>/`）で表示される。
未設定ならパスのみ表示される。

> Django admin でも確認できる。`Event` 一覧に `dashboard_token`、`Stall` 一覧に `access_token`
> を表示している（いずれも読み取り専用）。

## 6. 管理者（superuser）を作成

admin を使う場合のみ。Web Service の **Shell** から作成する（自動作成はしない）。

```bash
python manage.py createsuperuser
```

作成後、`https://<ホスト>/admin/` からログインできる。

---

## ローカルで本番相当の確認をする場合

`.env.example` を `.env` にコピーし、`DJANGO_DEBUG=False` 等を設定してから:

```bash
python manage.py collectstatic --noinput
python manage.py check --deploy
python manage.py runserver
```

※ `DEBUG=False` では HTTP が HTTPS にリダイレクトされる（`SECURE_SSL_REDIRECT`）。
ローカルの平文HTTPで画面確認したいだけなら、`.env` の `DJANGO_DEBUG=True` に戻す。
gunicorn は Windows では動かないため、ローカルでの起動確認は runserver で行う。

## 参考: 主な環境変数

`.env.example` を参照。開発時は `DATABASE_URL` を未設定にすれば SQLite で動作する。
