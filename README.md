# aapstats

Ansible Automation Platform（AAP）の移行で、移行するリソースを選ぶための判断材料を API から取得するツールです。API は GET だけを使い、AAP 上のリソースは変更しません。

要件は [requirements.md](requirements.md) にまとめています。

## 動作環境

- Python 3.9 以上
- `requests`（必須）
- `rich`（任意。入っていれば表の表示に使います）

```
pip install requests
```

## 接続の設定

`config.ini.example` を `~/.config/aapstats/config.ini` にコピーし、接続先を書きます。トークンは別ファイルに保存し、権限を 600 にします。

```
mkdir -p ~/.config/aapstats
cp config.ini.example ~/.config/aapstats/config.ini
chmod 600 ~/.config/aapstats/config.ini
```

設定ファイルを使わず、環境変数で指定することもできます。

| 環境変数 | 内容 |
|---|---|
| `AAP_HOST` | 接続先の URL |
| `AAP_TOKEN` / `AAP_TOKEN_FILE` | トークン / トークンを保存したファイル |
| `AAP_USERNAME` / `AAP_PASSWORD` | ユーザー名 / パスワード |
| `AAP_VERIFY_SSL` | `false` で証明書の検証を無効にする |
| `AAP_CA_FILE` | CA 証明書のファイル |

パスワードとトークンは、シェルの履歴に残らないよう、コマンドの引数では受け取りません。

実行ユーザーは、システム監査者（System Auditor）以上の権限を持つことを前提としています。

## 使い方

```
python3 -m aapstats --profile source info
python3 -m aapstats --profile source info --format json -o info.json
python3 -m aapstats --help
```

### info

接続先、判別したバージョン、リソースごとの件数、残っているジョブ履歴の期間、履歴の削除設定（Cleanup Job Details）を表示します。

### list

リソースの一覧を出します。参照先（プロジェクト、インベントリなど）は ID ではなく名前で表示します。

```
python3 -m aapstats --profile source list job_templates
python3 -m aapstats --profile source list credentials --wide --format csv -o credentials.csv
python3 -m aapstats --profile source list projects --columns name,scm_url,used_by_templates --lang ja
```

| リソース | 主な列 |
|---|---|
| `job_templates` | プロジェクト、Playbook、インベントリ、認証情報、実行環境、最終実行日時と結果 |
| `workflow_job_templates` | ノード数、ノードが呼び出すテンプレート、最終実行日時と結果 |
| `schedules` | 対象テンプレート、有効か無効か、次回実行日時、繰り返し設定 |
| `projects` | SCM の種類と URL、ブランチ、最終同期日時、使っているテンプレートの数 |
| `inventories` | 種類、ホスト数、インベントリソース、使っているテンプレートの数 |
| `credentials` | 認証情報タイプ、テンプレート・プロジェクト・インベントリソース・組織から使われている数 |
| `execution_environments` | イメージ、テンプレート・プロジェクト・組織・インベントリソースから使われている数 |
| `organizations` | 配下の各リソースの数 |

- `--wide` ですべての列を、`--columns` で指定した列だけを出します。
- `--format json` のときは、`--columns` を指定しない限りすべての列を出します。
- 認証情報の入力値（`inputs`）は、どの形式でも出力しません。

#### 絞り込み

```
# 90 日以上実行されておらず、有効なスケジュールからもワークフローからも呼ばれていないテンプレート
python3 -m aapstats list job_templates --unused-days 90 --orphan

# 8 月以降に一度も実行されていないテンプレート（実行回数はテンプレートごとに API で数える）
python3 -m aapstats list job_templates --since 2026-08-01 --max-runs 0

# 実行回数の多い順に 10 件（降順は --sort=-列名 と = でつなぐ）
python3 -m aapstats list job_templates --columns name,run_count --sort=-run_count --limit 10

# どこからも使われていない認証情報（組み込みのものは除く）
python3 -m aapstats list credentials --unreferenced

# 認証情報 linux-ssh を使っているテンプレート（名前か ID で指定）
python3 -m aapstats list job_templates --uses-credential linux-ssh
```

| 種類 | オプション | 対象 |
|---|---|---|
| 共通 | `--org`、`--name`、`--name-regex`、`--created-before/after`、`--modified-before/after`、`--sort`、`--limit` | すべて |
| 利用状況 | `--unused-days`、`--never-run`、`--last-status`、`--min-runs`、`--max-runs`、`--since` | テンプレート |
| 参照関係 | `--orphan`、`--no-schedule` | テンプレート |
| 参照関係 | `--unreferenced` | プロジェクト、インベントリ、認証情報、実行環境 |
| 逆引き | `--uses-project`、`--uses-inventory`、`--uses-credential`、`--uses-ee` | テンプレート |

- 複数の条件を指定したときは、すべてを満たすものを出します。
- `--unused-days` は、テンプレートの最終実行日時（`last_job_run`）で判定します。`last_job_run` は、古いジョブ履歴が削除されても残ります。
- `--orphan` は、**有効な**スケジュールだけを数えます。無効なスケジュールはテンプレートを起動しないためです。スケジュールが 1 つもないものは `--no-schedule` で絞り込みます。

### deps

ジョブテンプレートまたはワークフローを 1 つ指定し、移行先で動かすために一緒に移行が必要なリソースを出します。

```
python3 -m aapstats deps workflow_job_template "月次パッチ適用"
python3 -m aapstats deps jt 19 --format csv -o deps.csv
```

- 表示形式が `table` のときは、ツリーと、種類ごとにまとめた一覧を出します。同じリソースが 2 回目以降に出てきたときは、中身を展開せず「上に展開済み」と表示します。
- カスタムの認証情報タイプには「移行先で作り直しが必要」と注記します。
- `csv` は種類ごとにまとめた一覧だけを、`json` はツリーと一覧の両方を出します。

## テスト

```
python3 -m unittest discover -s tests -t .
```
