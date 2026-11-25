# aapstats

Ansible Automation Platform（AAP）の移行で、移行するリソースを選ぶための判断材料を API から取得するツールです。API は GET だけを使い、AAP 上のリソースは変更しません。

要件は [requirements.md](requirements.md) にまとめています。

## 動作環境

- Python 3.9 以上
- `requests`（必須）
- `rich`（任意。入っていれば表の表示に使います）

インターネットにつながらない環境を前提に、pip でのインストールは使いません。`requests` が入っているかは、次のコマンドで確かめます。

```
python3 -c 'import requests; print(requests.__version__)'
```

RHEL では `subscription-manager` が `requests` に依存しているため、多くの場合は最初から入っています。入っていなければ、社内のリポジトリ（Satellite など）から `sudo dnf install python3-requests` で入れます。

## 資材の配置と実行方法

### 資材を置く

資材は任意のディレクトリに置きます。以降は `/opt/aapstats` に置いた例で説明します。

インターネットにつながらない環境へは、つながる端末で資材を 1 つのファイルにまとめてから持ち込みます。

```
# インターネットにつながる端末で
git clone https://github.com/kifujii/aap_stats.git
cd aap_stats
git archive --format=tar.gz --prefix=aapstats/ -o aapstats.tar.gz main

# 持ち込んだ先で（/opt/aapstats に展開される）
sudo tar xzf aapstats.tar.gz -C /opt
```

### 資材のディレクトリに入って実行する

このツールはインストールせずに使うため、**資材を置いたディレクトリに入ってから実行します。** 別のディレクトリで実行すると、`No module named aapstats` というエラーになります。

```
cd /opt/aapstats
python3 -m aapstats --help
```

### エイリアスでどこからでも実行できるようにする

毎回ディレクトリを移動するのが手間なら、エイリアスを登録します。登録すると、どのディレクトリからでも `aapstats` だけで実行できます。

```
echo "alias aapstats='PYTHONPATH=/opt/aapstats python3 -m aapstats'" >> ~/.bashrc
source ~/.bashrc
aapstats --help
```

エイリアスを使うときは、次の 2 点に注意してください。

- エイリアスが効くのは、ログインして手でコマンドを打つときだけです。シェルスクリプト、cron、`sudo` の後ろでは効きません。これらの中で使うときは、`cd /opt/aapstats && python3 -m aapstats ...` のように書きます。
- `-o` で保存するファイルは、資材のディレクトリではなく、コマンドを実行したディレクトリに作られます。

以降の例は `python3 -m aapstats` で書いています。エイリアスを登録した場合は、`aapstats` に読み替えてください。

## 接続の設定

このツールは、Linux サーバーの各ユーザーが使う前提です。接続先の設定と AAP のトークンは、サーバーに 1 つだけ置き、全ユーザーで共有します。手元に保存するジョブ履歴（`sync` で作る SQLite）は、ユーザーごとに作られます。

| もの | 置き場所 | 持ち主 |
|---|---|---|
| 設定ファイル | `/etc/aapstats/config.ini` | 全ユーザーで共有 |
| AAP のトークン | `/etc/aapstats/<プロファイル名>.token` | 全ユーザーで共有 |
| ジョブ履歴 | `~/.local/share/aapstats/<プロファイル名>.sqlite` | ユーザーごと |

### 管理者が行うこと

**1. AAP でトークンを作る**

このツール専用のユーザーを AAP に作り、システム監査者（System Auditor）の権限を付けてから、そのユーザーで Read スコープのトークンを作ることを勧めます。admin のトークンを使うこともできますが、次の 2 つの理由から勧めません。

- admin のトークンは、Read スコープでも admin が見られるものをすべて読めます。このツールを使える Linux ユーザー全員が、その範囲を読めることになります。
- 専用のユーザーにしておくと、AAP の操作の記録で、このツールからのアクセスだと見分けられます。

**2. ツールを使うユーザーのグループを作る**

設定ファイルとトークンは、このグループのユーザーだけが読めるようにします。

```
sudo groupadd aapstats
sudo usermod -aG aapstats <ユーザー名>    # ツールを使うユーザーごとに実行する
```

グループへの追加は、そのユーザーが次にログインしたときから有効になります。

**3. 設定ファイルを置く**

```
sudo install -d -m 750 -g aapstats /etc/aapstats
sudo install -m 640 -g aapstats /opt/aapstats/config.ini.example /etc/aapstats/config.ini
sudo vi /etc/aapstats/config.ini
```

`host` に接続先を、`token_file` にトークンのファイルを書きます。`token_file` は必ず絶対パスで書いてください。`~` で書くと、コマンドを実行したユーザーのホームディレクトリと解釈され、ユーザーごとに別の場所を探してしまいます。

```
[source]
host = https://aap-source.example.com
token_file = /etc/aapstats/source.token
```

**4. トークンを保存する**

最初に権限 640 の空のファイルを作り、そこへ書き込みます。トークンを書き込んでから権限を変えると、その間だけ他のユーザーにも読める状態になるためです。`read -rs` で入力した値は画面に表示されず、シェルの履歴にも残りません。

```
sudo install -m 640 -g aapstats /dev/null /etc/aapstats/source.token
read -rs AAPTOK && printf '%s' "$AAPTOK" | sudo tee /etc/aapstats/source.token > /dev/null && unset AAPTOK
```

移行先（`[target]`）も使う場合は、同じ手順で `target.token` を作ります。

### 各ユーザーが行うこと

共通の設定ファイルの場所を、環境変数 `AAPSTATS_CONFIG` で指定します。

```
echo "export AAPSTATS_CONFIG=/etc/aapstats/config.ini" >> ~/.bashrc
source ~/.bashrc
cd /opt/aapstats
python3 -m aapstats --profile source info
```

- `AAPSTATS_CONFIG` を設定しないと、ツールは自分のホームの `~/.config/aapstats/config.ini` を探します。
- `~/.bashrc` は、cron やシェルスクリプトでは読まれません。そこで使うときは、`AAPSTATS_CONFIG=/etc/aapstats/config.ini` をコマンドの前に付けます。
- ジョブ履歴はユーザーごとに作られます。各ユーザーが `sync` すると、そのたびに AAP から履歴を取得するため、AAP の負荷はユーザーの数だけ増えます。履歴が多い場合は、初回の `sync` に `--since` を付けて、必要な期間だけを取得してください。

### 環境変数

設定ファイルの代わりに、環境変数で指定することもできます。

| 環境変数 | 内容 |
|---|---|
| `AAPSTATS_CONFIG` | 設定ファイルのパス（既定: `~/.config/aapstats/config.ini`） |
| `AAPSTATS_DATA_DIR` | ジョブ履歴の保存先のディレクトリ（既定: `~/.local/share/aapstats`） |
| `AAP_HOST` | 接続先の URL |
| `AAP_TOKEN` / `AAP_TOKEN_FILE` | トークン / トークンを保存したファイル |
| `AAP_USERNAME` / `AAP_PASSWORD` | ユーザー名 / パスワード |
| `AAP_VERIFY_SSL` | `false` で証明書の検証を無効にする |
| `AAP_CA_FILE` | CA 証明書のファイル |

パスワードとトークンは、シェルの履歴に残らないよう、コマンドの引数では受け取りません。

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
| `teams` | メンバー数、ロールとその対象（例: `JobTemplate Execute: patch`） |
| `credential_types` | カスタム認証情報タイプの入力項目名、秘密の入力項目名、インジェクターの環境変数名、使っている認証情報の数 |
| `authenticators` | Authentication Method の種類、有効か無効か、優先順位、LDAP のサーバー URI・Bind DN・検索条件、マッピング数 |
| `authenticator_maps` | 認証のマッピング（組織・チーム・ロールへの対応と条件） |

- `--wide` ですべての列を、`--columns` で指定した列だけを出します。
- `--format json` のときは、`--columns` を指定しない限りすべての列を出します。
- 認証情報の入力値（`inputs`）は、どの形式でも出力しません。
- Authentication Method とマッピングの設定のうち、名前に PASSWORD、SECRET、TOKEN、KEY、PRIVATE を含む項目と、値が `$encrypted$` の項目は伏せます。伏せた項目の名前は `masked_keys` 列に出します。移行先で入力し直す必要がある項目です。
- AAP 2.4 以前では、Authentication Method とマッピングを Controller の LDAP 設定から作ります。追加の LDAP サーバー（1〜5）も 1 行ずつ出します。

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

### sync

ジョブ履歴を手元の SQLite に保存します。`stats` はこの保存したデータから統計を出します。

```
python3 -m aapstats --profile source sync
python3 -m aapstats --profile source sync --since 2026-08-01 --kinds job,workflow_job,project_update
```

- 保存先の既定は、実行したユーザーの `~/.local/share/aapstats/<プロファイル名>.sqlite` です。履歴はユーザーごとに別のファイルになります。`--db` で変えられます。接続先ごとにファイルを分け、別の接続先の履歴が混ざりそうなときはエラーにします。
- 初回は残っている履歴をすべて取得します。2 回目以降は、新しいジョブと、前回の時点で終わっていなかったジョブだけを取得します。
- 1 ページ（200 件）ごとに保存するので、途中で止まっても、次回は続きから取得します。進捗は 10 秒ごとに、残り件数と完了までの目安とともに表示します。
- AAP 側で古い履歴が削除されても、保存済みの履歴は消しません。定期的に `sync` すれば、AAP の保持期間より長い期間の統計を取れます。
- 保存したファイルにはお客様環境のデータが含まれるため、権限を 600 にします。

### stats

保存したジョブ履歴から統計を出します。AAP には接続しないため、トークンがなくても動きます。

```
python3 -m aapstats --profile source stats
python3 -m aapstats --profile source stats --since 2026-08-01 --until 2026-11-01
python3 -m aapstats --profile source stats --group-by month
python3 -m aapstats --profile source stats --top 10
python3 -m aapstats --profile source stats --format csv -o stats.csv --wide
```

- 既定はテンプレートごとの統計です。実行回数、結果の内訳と成功率、実行時間（平均・最大・合計）、起動方法の内訳（手動、スケジュール、ワークフロー、再実行、コールバック、Webhook）、手動で実行したユーザー数、期間内の初回・最終の実行日時を出します。
- 削除済みのテンプレートの履歴は、起動した時点のテンプレート名で集計し、`template_deleted` を `true` にします。
- `--group-by` で集計の単位を変えます。`organization`、`project`、`inventory`、`execution_environment`、`credential`、`day`、`week`、`month`、`hour`、`weekday` を指定できます。日時の区切りは `--tz`（既定: Asia/Tokyo）に従います。
- `--top N` で、実行回数、失敗率、平均実行時間の上位 N 件を出します。`--by` で 1 つに絞れます。`--min-runs` で、実行回数の少ないテンプレートを除けます。
- 結果の冒頭に、集計した期間、保存済みの期間、ジョブ数を出します。
- 100 万件の履歴で、テンプレート別の集計は 15 秒前後、メモリは 30MB 程度です（認証情報別は 30 秒前後）。

### report

テンプレートと関連リソースを「移行対象」「要確認」「移行不要候補」に分類し、理由を付けて出します。ツールが出すのは候補です。移行するかどうかは人が決めます。

```
python3 -m aapstats --profile source report
python3 -m aapstats --profile source report --format csv -o report.csv --lang ja
python3 -m aapstats --profile source report --unused-days 180 --only review,unneeded
```

**テンプレートの判定**（既定は 90 日。`--unused-days` か、設定ファイルのプロファイルの `unused_days` で変えられます）

| 分類 | 条件 |
|---|---|
| 移行対象 | 直近 90 日以内に実行されている |
| 要確認 | 直近 90 日の実行がすべて失敗している。または、90 日以上実行されていないが、有効なスケジュールか、移行不要候補でないワークフローから呼ばれている |
| 移行不要候補 | 90 日以上実行されておらず、どこからも呼ばれていない |

- 呼び出し元のワークフローが移行不要候補なら、呼ばれていないものとして扱います。使われていないワークフローの部品が、要確認に残り続けないようにするためです。
- 「すべて失敗」の判定では、直近に実行したテンプレートごとに API で件数を数えます。省くときは `--no-failure-check` を付けます。

**関連リソースの判定**

プロジェクト、インベントリ、認証情報、認証情報タイプ（カスタムのもの）、実行環境、チームは、使っているもののうち、いちばん強い判定（移行対象 > 要確認 > 移行不要候補）を引き継ぎます。どこからも使われていなければ移行不要候補です。

- テンプレート以外からの参照も数えます。プロジェクトの SCM 認証情報、インベントリソース、ワークフローの同期ノード、実行環境のレジストリ認証情報などです。
- 組織の Galaxy 認証情報と、組織の既定の実行環境は、移行対象にします。組織は移行する前提のためです。
- ジョブテンプレートに実行環境を指定していないときは、AAP と同じ順（プロジェクトの既定 → 組織の既定 → 全体の既定）で実行環境を決めます。全体の既定も設定されていないときは、最後のジョブが実際に使った実行環境を参照先にします。
- 認証のマッピング（LDAP など）で使われているチームは、移行対象にします。
- AAP の組み込みのリソースは判定しません。

**出力**

- `table`: 判定の条件、サマリー（種類ごとの件数と分類の内訳）、種類ごとの判定結果
- `csv`: 判定結果を 1 つの表にまとめ、空欄の `decision` 列を付けます。Excel で移行するかどうかを書き込むためです。
- `json`: `conditions`、`summary`、`items`
- `--only` と `--resource` で出す行を絞れます。サマリーは、絞り込む前の全体で出します。

## テスト

資材のディレクトリで実行します。

```
cd /opt/aapstats
python3 -m unittest discover -s tests -t .
```
