# aapstats

Ansible Automation Platform（AAP）の移行で、移行するリソースを選ぶための判断材料を API から取得するツールです。API は GET だけを使い、AAP 上のリソースは変更しません。

要件は [requirements.md](requirements.md) にまとめています。

## 目次

- [置き場所の全体像](#置き場所の全体像)（[設定ファイルを探す順番](#設定ファイルを探す順番) / [プロファイルの選び方](#プロファイルの選び方)）

**導入（管理者が 1 回だけ行う）**

- [1. 動作環境を確かめる](#1-動作環境を確かめる)
- [2. 資材を置く](#2-資材を置く)
- [3. AAP でトークンを作る](#3-aap-でトークンを作る)
- [4. 設定ファイルを置く](#4-設定ファイルを置く)
- [5. トークンを保存する](#5-トークンを保存する)

**利用の準備（各ユーザーが行う）**

- [6. 共通の設定を指定して実行する](#6-共通の設定を指定して実行する)

**任意の設定**（中身は折りたたんでいます）

- [root 権限がない場合はホームに置く](#root-権限がない場合はホームに置く)
- [グループでトークンを読めるユーザーを絞る](#グループでトークンを読めるユーザーを絞る)
- [エイリアスでどこからでも実行する](#エイリアスでどこからでも実行する)
- [cron で定期実行する](#cron-で定期実行する)
- [資材を更新する](#資材を更新する)
- [設定ファイルに書ける項目](#設定ファイルに書ける項目)
- [環境変数で指定する](#環境変数で指定する)

**コマンドの使い方**

- [共通のオプション](#共通のオプション)
- [info](#info) / [list](#list) / [絞り込み](#絞り込み) / [deps](#deps) / [sync](#sync) / [stats](#stats)（[ワークフローを集計する](#ワークフローを集計する)） / [report](#report)
- [テスト](#テスト)

## 置き場所の全体像

このツールは、Linux サーバーの各ユーザーが使う前提です。資材、接続先の設定、AAP のトークンはサーバーに 1 つだけ置き、全ユーザーで共有します。手元に保存するジョブ履歴（`sync` で作る SQLite）は、ユーザーごとに作られます。

| もの | 置き場所 | 持ち主 |
|---|---|---|
| 資材 | `/opt/aapstats` | 全ユーザーで共有 |
| 設定ファイル | `/etc/aapstats/config.ini` | 全ユーザーで共有 |
| AAP のトークン | `/etc/aapstats/<プロファイル名>.token` | 全ユーザーで共有 |
| ジョブ履歴 | `~/.local/share/aapstats/<プロファイル名>.sqlite` | ユーザーごと |

置き場所は変えられます。以降は上の表の場所で説明します。

### 設定ファイルを探す順番

ツールは、設定ファイルを次の順で探し、最初に見つかったものを使います。

1. `--config` オプションで指定した場所
2. 環境変数 `AAPSTATS_CONFIG` で指定した場所
3. どちらもなければ、実行したユーザーの `~/.config/aapstats/config.ini`

導入手順では、2 の `AAPSTATS_CONFIG` で `/etc/aapstats/config.ini` を指定し、全ユーザーで同じ設定を使います（手順 6）。

root 権限がなく、`/etc` や `/opt` にディレクトリを作れない場合は、3 の既定の場所を使います。資材、設定、トークン、履歴をすべて自分のホームに置けば、root 権限なしで使えます。手順は「[root 権限がない場合はホームに置く](#root-権限がない場合はホームに置く)」にあります。

### プロファイルの選び方

設定ファイルには、接続先ごとに「プロファイル」を書きます。`[source]` や `[target]` の部分がプロファイルの名前です。どのプロファイルを使うかは、`--profile` で指定します。

```
python3 -m aapstats --profile source info     # [source] の接続先を使う
python3 -m aapstats --profile target info     # [target] の接続先を使う
```

**`--profile` を省略したとき**

`[default]` という名前のプロファイルを使います。`[default]` がなければ、登録済みのプロファイルを示すエラーになります。

```
エラー: プロファイルが指定されていません。--profile で次のいずれかを指定してください: source, target
```

よく使う AAP のプロファイルを `[default]` という名前にしておくと、`--profile` を省略できます。たとえば移行元をよく使うなら、次のように書きます。

```
[default]
# 移行元（--profile を省略したときに使う）
host = https://aap-source.example.com
token_file = /etc/aapstats/source.token

[target]
# 移行先（--profile target で使う）
host = https://aap-target.example.com
token_file = /etc/aapstats/target.token
```

大文字の `[DEFAULT]` でも省略できますが、勧めません。大文字の `[DEFAULT]` に書いた値は、ほかのすべてのプロファイルに引き継がれるためです。たとえば `[DEFAULT]` に移行元の `host` を書き、`[target]` に `host` を書き忘れると、`--profile target` を指定しても移行元につながってしまいます。

**ジョブ履歴のファイル名**

`sync` で保存するジョブ履歴のファイル名は、プロファイルの名前で決まります。

| 指定のしかた | ファイル名 |
|---|---|
| `--profile source` | `source.sqlite` |
| `--profile` を省略して `[default]` を使った | `default.sqlite` |
| プロファイルを使わず、環境変数 `AAP_HOST` で接続先を指定した | 接続先のホスト名（例: `aap-source.example.com.sqlite`） |

**同じ AAP を 2 つのプロファイルに書いたときの影響**

同じ AAP を、たとえば `[default]` と `[source]` の両方に書くと、次の影響があります。

1. **履歴が 2 つのファイルに分かれます。** `default.sqlite` と `source.sqlite` が別々に作られます。
2. **AAP への負荷と取得時間が 2 倍になります。** それぞれのプロファイルで `sync` すると、同じ履歴を 2 回取得します。特に初回は残っている履歴をすべて取得するため、影響が大きくなります。手元のディスクの使用量も 2 倍になります。
3. **`stats` の結果が、使ったプロファイルによって変わります。** cron で一方のプロファイルだけ `sync` していると、もう一方は古い履歴のまま集計されます。どの時点の履歴で集計したかは、`stats` の冒頭の「集計の条件」にある `last_sync`（最終取得日時）で確かめられます。

`list`、`deps`、`report`、`info` は、毎回 AAP から取得するため、この影響は受けません。

同じ AAP は、1 つのプロファイルにだけ書いてください。`--profile` を省略したい場合は、その AAP のプロファイルの名前を `[default]` にします。

## 導入（管理者が 1 回だけ行う）

### 1. 動作環境を確かめる

- Python 3.9 以上
- `requests`（必須）
- `rich`（任意。入っていれば表の表示に使います）

インターネットにつながらない環境を前提に、pip でのインストールは使いません。`requests` が入っているかは、次のコマンドで確かめます。

```
python3 -c 'import requests; print(requests.__version__)'
```

RHEL では `subscription-manager` が `requests` に依存しているため、多くの場合は最初から入っています。入っていなければ、社内のリポジトリ（Satellite など）から `sudo dnf install python3-requests` で入れます。

### 2. 資材を置く

インターネットにつながる端末で、GitHub のリポジトリの画面から「Code」→「Download ZIP」を選び、`aap_stats-main.zip` をダウンロードします。このファイルを、許可された方法で閉域の環境へ持ち込みます。

閉域の環境で展開します。root 権限がなく `/opt` に置けない場合は、「[root 権限がない場合はホームに置く](#root-権限がない場合はホームに置く)」の手順を使います。zip の中には `aap_stats-main` というディレクトリが入っているので、展開してから名前を `aapstats` に変えます。

```
sudo unzip -q aap_stats-main.zip -d /opt
sudo mv /opt/aap_stats-main /opt/aapstats
ls /opt/aapstats        # README.md、aapstats、config.ini.example などがあれば成功
```

<details>
<summary>unzip コマンドがない場合</summary>

Python の標準機能で展開できます。

```
sudo python3 -m zipfile -e aap_stats-main.zip /opt
sudo mv /opt/aap_stats-main /opt/aapstats
```

</details>

### 3. AAP でトークンを作る

このツール専用のユーザーを AAP に作り、システム監査者（System Auditor）の権限を付けます。そのユーザーで、Read スコープのトークンを作ります。

<details>
<summary>admin のトークンを勧めない理由</summary>

admin のトークンを使うこともできますが、次の 2 つの理由から勧めません。

- admin のトークンは、Read スコープでも admin が見られるものをすべて読めます。トークンのファイルを読める Linux ユーザー全員が、その範囲を読めることになります。
- 専用のユーザーにしておくと、AAP の操作の記録で、このツールからのアクセスだと見分けられます。

</details>

### 4. 設定ファイルを置く

root 権限がなく `/etc` にディレクトリを作れない場合は、手順 4〜6 の代わりに「[root 権限がない場合はホームに置く](#root-権限がない場合はホームに置く)」の手順を使います。

```
sudo install -d -m 755 /etc/aapstats
sudo install -m 644 /opt/aapstats/config.ini.example /etc/aapstats/config.ini
sudo vi /etc/aapstats/config.ini
```

`host` に接続先を、`token_file` にトークンのファイルを書きます。`token_file` は必ず絶対パスで書いてください。`~` で書くと、コマンドを実行したユーザーのホームディレクトリと解釈され、ユーザーごとに別の場所を探してしまいます。

```
[source]
host = https://aap-source.example.com
token_file = /etc/aapstats/source.token
```

プロファイルの名前を `[default]` にすると、`--profile` を省略できます（「[プロファイルの選び方](#プロファイルの選び方)」）。

この手順では、設定ファイルとトークンを、サーバーにログインできる全ユーザーが読めるようにします。ツールを使うユーザーだけに絞りたい場合は、手順 4 と 5 の代わりに「[グループでトークンを読めるユーザーを絞る](#グループでトークンを読めるユーザーを絞る)」のコマンドを使います。

### 5. トークンを保存する

最初に空のファイルを作り、そこへ書き込みます。`read -rs` で入力した値は画面に表示されず、シェルの履歴にも残りません。

```
sudo install -m 644 /dev/null /etc/aapstats/source.token
read -rs AAPTOK && printf '%s' "$AAPTOK" | sudo tee /etc/aapstats/source.token > /dev/null && unset AAPTOK
```

移行先（`[target]`）も使う場合は、同じ手順で `target.token` を作ります。

## 利用の準備（各ユーザーが行う）

### 6. 共通の設定を指定して実行する

共通の設定ファイルの場所を、環境変数 `AAPSTATS_CONFIG` で指定します。

```
echo "export AAPSTATS_CONFIG=/etc/aapstats/config.ini" >> ~/.bashrc
source ~/.bashrc
```

このツールはインストールせずに使うため、**資材のディレクトリに入ってから実行します。** 別のディレクトリで実行すると、`No module named aapstats` というエラーになります。

```
cd /opt/aapstats
python3 -m aapstats --profile source info
```

- `AAPSTATS_CONFIG` を設定しないと、ツールは自分のホームの `~/.config/aapstats/config.ini` を探します。
- ジョブ履歴はユーザーごとに作られます。各ユーザーが `sync` すると、そのたびに AAP から履歴を取得するため、AAP の負荷はユーザーの数だけ増えます。履歴が多い場合は、初回の `sync` に `--since` を付けて、必要な期間だけを取得してください。
- 毎回ディレクトリを移動するのが手間なら、「[エイリアスでどこからでも実行する](#エイリアスでどこからでも実行する)」を設定します。

## 任意の設定

### root 権限がない場合はホームに置く

<details>
<summary>手順を表示</summary>

root 権限がなく、`/opt` や `/etc` にディレクトリを作れない場合は、すべてを自分のホームに置きます。設定ファイルを既定の場所（`~/.config/aapstats/config.ini`）に置くので、`AAPSTATS_CONFIG` の指定は要りません。

| もの | 置き場所 |
|---|---|
| 資材 | `~/aapstats` |
| 設定ファイル | `~/.config/aapstats/config.ini` |
| AAP のトークン | `~/.config/aapstats/source.token` |
| ジョブ履歴 | `~/.local/share/aapstats/source.sqlite` |

この置き方では、設定とトークンを自分だけが読めるようにします（権限 600）。ほかのユーザーと共有はしません。ほかのユーザーも使う場合は、各自が同じ手順で自分のホームに置きます。

**1. 資材を置く**（手順 2 の代わり）

```
unzip -q aap_stats-main.zip -d ~
mv ~/aap_stats-main ~/aapstats
```

**2. 設定ファイルを置く**（手順 4 の代わり）

```
mkdir -p -m 700 ~/.config/aapstats
install -m 600 ~/aapstats/config.ini.example ~/.config/aapstats/config.ini
vi ~/.config/aapstats/config.ini
```

`host` に接続先を、`token_file` にトークンのファイルを書きます。自分だけが読む設定なので、`~` を使って書けます。

```
[source]
host = https://aap-source.example.com
token_file = ~/.config/aapstats/source.token
```

**3. トークンを保存する**（手順 5 の代わり）

```
install -m 600 /dev/null ~/.config/aapstats/source.token
read -rs AAPTOK && printf '%s' "$AAPTOK" > ~/.config/aapstats/source.token && unset AAPTOK
```

**4. 実行する**（手順 6 の代わり）

```
cd ~/aapstats
python3 -m aapstats --profile source info
```

**エイリアスと cron の書き方**

資材の場所が `~/aapstats` になり、`AAPSTATS_CONFIG` が要らなくなります。

```
# エイリアス（~/.bashrc に追加）
alias aapstats='PYTHONPATH=$HOME/aapstats python3 -m aapstats'

# cron（crontab -e で追加）
0 2 * * * PYTHONPATH=$HOME/aapstats /usr/bin/python3 -m aapstats --profile source sync >> $HOME/aapstats-sync.log 2>&1
```

</details>

### グループでトークンを読めるユーザーを絞る

<details>
<summary>手順を表示</summary>

ツールが動くために必要なのは、実行したユーザーが設定ファイルとトークンのファイルを読めることだけです。グループは必須ではありません。グループを作るかどうかで、トークンを読めるユーザーの範囲が変わります。

| | トークンを読めるユーザー | 設定ファイルとトークンの権限 |
|---|---|---|
| グループを作らない（手順 4・5 のとおり） | **サーバーにログインできる全ユーザー** | 644（全ユーザーが読める） |
| グループを作る（この手順） | グループに入れたユーザーだけ | 640（持ち主の root とグループだけが読める） |

グループを作らない場合、このツールを使わないユーザーも AAP のトークンを読めるようになり、そのトークンで AAP の API を読めます。サーバーにログインできるのが、ツールを使ってよい人だけであれば、グループを作らなくても問題ありません。そうでなければ、グループを作ることを勧めます。

**グループを作る**

グループへの追加は、そのユーザーが次にログインしたときから有効になります。

```
sudo groupadd aapstats
sudo usermod -aG aapstats <ユーザー名>    # ツールを使うユーザーごとに実行する
```

**設定ファイルを置く（手順 4 の代わり）**

```
sudo install -d -m 750 -g aapstats /etc/aapstats
sudo install -m 640 -g aapstats /opt/aapstats/config.ini.example /etc/aapstats/config.ini
sudo vi /etc/aapstats/config.ini
```

**トークンを保存する（手順 5 の代わり）**

先に権限 640 の空のファイルを作ってから書き込みます。トークンを書き込んでから権限を変えると、その間だけ他のユーザーにも読める状態になるためです。

```
sudo install -m 640 -g aapstats /dev/null /etc/aapstats/source.token
read -rs AAPTOK && printf '%s' "$AAPTOK" | sudo tee /etc/aapstats/source.token > /dev/null && unset AAPTOK
```

すでに手順 4・5 で置いたファイルを絞る場合は、権限を変えます。

```
sudo chgrp aapstats /etc/aapstats /etc/aapstats/*
sudo chmod 750 /etc/aapstats
sudo chmod 640 /etc/aapstats/*
```

</details>

### エイリアスでどこからでも実行する

<details>
<summary>手順を表示</summary>

エイリアスを登録すると、どのディレクトリからでも `aapstats` だけで実行できます。

```
echo "alias aapstats='PYTHONPATH=/opt/aapstats python3 -m aapstats'" >> ~/.bashrc
source ~/.bashrc
aapstats --help
```

エイリアスを使うときは、次の 2 点に注意してください。

- エイリアスが効くのは、ログインして手でコマンドを打つときだけです。シェルスクリプト、cron、`sudo` の後ろでは効きません。cron での書き方は「[cron で定期実行する](#cron-で定期実行する)」を参照してください。
- `-o` で保存するファイルは、資材のディレクトリではなく、コマンドを実行したディレクトリに作られます。

</details>

### cron で定期実行する

<details>
<summary>手順を表示</summary>

AAP 側の古いジョブ履歴は、保持期間（初期値は 120 日）を過ぎると削除されます。`sync` を cron で毎晩実行しておくと、削除される前の履歴を手元に残し続けられます。

cron では、エイリアスと `~/.bashrc` が使えません。そのため、共通の設定ファイルの場所（`AAPSTATS_CONFIG`）と資材の場所を、cron の行の中で指定します。書き方は次の 2 通りです。どちらも、`crontab -e` で各ユーザーの crontab に書きます。

**書き方 1：資材のディレクトリに移動してから実行する**

```
# 毎日 2:00 に、移行元の履歴を取得する
0 2 * * * cd /opt/aapstats && AAPSTATS_CONFIG=/etc/aapstats/config.ini /usr/bin/python3 -m aapstats --profile source sync >> $HOME/aapstats-sync.log 2>&1
```

**書き方 2：移動せず、フルパスで実行する**

`PYTHONPATH` で資材の場所を指定すると、ディレクトリを移動しなくても実行できます。

```
# 毎日 2:00 に、移行元の履歴を取得する
0 2 * * * AAPSTATS_CONFIG=/etc/aapstats/config.ini PYTHONPATH=/opt/aapstats /usr/bin/python3 -m aapstats --profile source sync >> $HOME/aapstats-sync.log 2>&1

# 毎週月曜 7:00 に、移行判定の CSV を日付入りのファイル名で保存する
0 7 * * 1 AAPSTATS_CONFIG=/etc/aapstats/config.ini PYTHONPATH=/opt/aapstats /usr/bin/python3 -m aapstats --profile source report --format csv -o $HOME/aapstats/report-$(date +\%Y\%m\%d).csv >> $HOME/aapstats-report.log 2>&1
```

共通の値を crontab の先頭に書いておくと、各行を短くできます。

```
AAPSTATS_CONFIG=/etc/aapstats/config.ini
PYTHONPATH=/opt/aapstats

0 2 * * * /usr/bin/python3 -m aapstats --profile source sync >> $HOME/aapstats-sync.log 2>&1
```

**cron で気をつけること**

- **`python3` はフルパスで書きます。** cron の `PATH` は最小限（`/usr/bin:/bin`）です。`python3` の場所は `command -v python3` で確かめられます。RHEL では `/usr/bin/python3` です。
- **`%` は `\%` と書きます。** crontab では `%` が改行の意味になるためです。上の例の `date +\%Y\%m\%d` がこれに当たります。
- **`-o` の保存先は絶対パスで書きます。** 書き方 1 で相対パスにすると、資材のディレクトリ（`/opt/aapstats`）に保存しようとして失敗します。一般ユーザーは、資材のディレクトリに書き込めないためです。保存先のディレクトリ（上の例では `$HOME/aapstats`）は、先に作っておきます。
- **ログを残します。** 上の例のように `>> ログファイル 2>&1` を付けると、進捗とエラーが残ります。
- **`sync` の二重起動を防ぎます。** 初回の `sync` は長くかかることがあります。前の `sync` が終わる前に次が始まらないよう、`flock -n` を前に付けておくと安全です。次の例は、crontab の先頭に `AAPSTATS_CONFIG` と `PYTHONPATH` を書いた場合の形です。

```
0 2 * * * flock -n $HOME/.aapstats-sync.lock /usr/bin/python3 -m aapstats --profile source sync >> $HOME/aapstats-sync.log 2>&1
```

</details>

### 資材を更新する

<details>
<summary>手順を表示</summary>

新しい zip を展開し、`/opt/aapstats` を置き換えます。設定ファイル（`/etc/aapstats`）と各ユーザーのジョブ履歴（`~/.local/share/aapstats`）は資材のディレクトリの外にあるため、置き換えても消えません。

```
sudo unzip -q aap_stats-main.zip -d /tmp
sudo rm -rf /opt/aapstats.old && sudo mv /opt/aapstats /opt/aapstats.old
sudo mv /tmp/aap_stats-main /opt/aapstats
```

</details>

### 設定ファイルに書ける項目

<details>
<summary>一覧を表示</summary>

設定ファイルのプロファイル（`[source]` など）に書ける項目です。コマンドのオプションで指定した値が、設定ファイルより優先します。設定ファイルにない項目は、環境変数から読みます。

| 項目 | 内容 | 既定 | 対応するオプション / 環境変数 |
|---|---|---|---|
| `host` | 接続先の URL | （必須） | `--host` / `AAP_HOST` |
| `token_file` | トークンを保存したファイル。共有する設定では絶対パスで書く | なし | `AAP_TOKEN_FILE` |
| `token` | トークンそのもの。ファイルに分けられないとき用で、勧めない | なし | `AAP_TOKEN` |
| `username` / `password` | トークンの代わりに、ユーザー名とパスワードで認証する | なし | `AAP_USERNAME` / `AAP_PASSWORD` |
| `password_file` | パスワードを保存したファイル | なし | `AAP_PASSWORD_FILE` |
| `verify_ssl` | `false` で証明書の検証をしない | `true` | `--insecure` / `AAP_VERIFY_SSL` |
| `ca_file` | 社内 CA の証明書ファイル | なし | `--ca-file` / `AAP_CA_FILE` |
| `api_version` | API の構成（`auto`、`gateway`、`legacy`） | `auto` | `--api-version` / `AAP_API_VERSION` |
| `timezone` | 日時を表示するタイムゾーン | `Asia/Tokyo` | `--tz` / `AAP_TIMEZONE` |
| `timeout` | 1 回の問い合わせを待つ秒数 | 60 | `--timeout` / `AAP_TIMEOUT` |
| `max_workers` | 同時に送る問い合わせの数 | 4 | `--max-workers` / `AAP_MAX_WORKERS` |
| `request_interval` | 問い合わせの最小間隔の秒数 | 0 | `--request-interval` / `AAP_REQUEST_INTERVAL` |
| `unused_days` | `report` で未使用とみなす日数 | 90 | `--unused-days` / `AAP_UNUSED_DAYS` |

トークンとユーザー名・パスワードの両方を書いた場合は、トークンを使います。

</details>

### 環境変数で指定する

<details>
<summary>一覧を表示</summary>

設定ファイルの代わりに、環境変数で指定することもできます。

| 環境変数 | 内容 |
|---|---|
| `AAPSTATS_CONFIG` | 設定ファイルのパス（既定: `~/.config/aapstats/config.ini`） |
| `AAPSTATS_DATA_DIR` | ジョブ履歴の保存先のディレクトリ（既定: `~/.local/share/aapstats`） |
| `AAP_HOST` | 接続先の URL |
| `AAP_TOKEN` / `AAP_TOKEN_FILE` | トークン / トークンを保存したファイル |
| `AAP_USERNAME` / `AAP_PASSWORD` | ユーザー名 / パスワード |
| `AAP_PASSWORD_FILE` | パスワードを保存したファイル |
| `AAP_VERIFY_SSL` | `false` で証明書の検証を無効にする |
| `AAP_CA_FILE` | CA 証明書のファイル |
| `AAP_API_VERSION` | API の構成（`auto`、`gateway`、`legacy`） |
| `AAP_TIMEZONE` | 日時を表示するタイムゾーン |
| `AAP_TIMEOUT` / `AAP_MAX_WORKERS` / `AAP_REQUEST_INTERVAL` | 問い合わせの待ち時間 / 同時に送る数 / 最小間隔 |
| `AAP_UNUSED_DAYS` | `report` で未使用とみなす日数 |

各項目の意味と既定値は「[設定ファイルに書ける項目](#設定ファイルに書ける項目)」を参照してください。

パスワードとトークンは、シェルの履歴に残らないよう、コマンドの引数では受け取りません。

</details>

## コマンドの使い方

以降の例は、資材のディレクトリで `python3 -m aapstats` と打つ形で書いています。エイリアスを登録した場合は、`aapstats` に読み替えてください。

```
python3 -m aapstats --profile source info
python3 -m aapstats --profile source info --format json -o info.json
python3 -m aapstats --help
```

### 共通のオプション

すべてのコマンドで使えるオプションです。サブコマンドの前にも後ろにも書けます（`--profile source list ...` と `list ... --profile source` は同じです）。

| オプション | できること |
|---|---|
| `--profile NAME` | 使うプロファイル（設定ファイルの `[NAME]`）を選ぶ。省略すると `[default]` を使う（「[プロファイルの選び方](#プロファイルの選び方)」） |
| `--format table\|csv\|json` | 出力形式を選ぶ。既定は `table`（画面表示用の表）。`csv` は BOM 付き UTF-8 で、Excel でそのまま開ける。`json` は他のツールやスクリプトに渡す用 |
| `-o PATH`、`--output PATH` | 結果をファイルに保存する。省略すると画面に出す。相対パスは、コマンドを実行したディレクトリが基準 |
| `--lang en\|ja` | 列名の言語。既定は `en`（API の項目名）。`ja` で日本語の列名にする（`table` と `csv` だけ。`json` のキーは英語のまま） |
| `--tz ZONE` | 日時を表示するタイムゾーン。既定は `Asia/Tokyo`。日付で絞り込むときの区切りにも使う |
| `-v`、`--verbose` | 詳細ログを出す。API の URL と応答時間が分かる。ログは標準エラーに出るので、`-o` やパイプの結果には混ざらない |
| `-h`、`--help` | 使い方を表示する。`python3 -m aapstats list --help` のように、サブコマンドごとにも表示できる |

<details>
<summary>接続に関するオプション</summary>

ふだんは設定ファイルに書いておき、一時的に変えたいときにコマンドで指定します。コマンドで指定した値は、設定ファイルと環境変数より優先します。

| オプション | できること | 既定 |
|---|---|---|
| `--config PATH` | 使う設定ファイルを指定する（「[設定ファイルを探す順番](#設定ファイルを探す順番)」） | `~/.config/aapstats/config.ini` |
| `--host URL` | 接続先の URL を指定する。トークンは設定ファイルか環境変数から読む | 設定ファイルの `host` |
| `--insecure` | 証明書の検証をしない。自己署名の証明書で一時的に試すとき用 | 検証する |
| `--ca-file PATH` | 社内 CA の証明書ファイルを指定する。証明書の検証は行ったまま、社内 CA を信頼する | なし |
| `--api-version auto\|gateway\|legacy` | API の構成。`auto` は自動で判別する。判別できないときに、`gateway`（AAP 2.5 以降）か `legacy`（AAP 2.4 以前）を指定する | `auto` |
| `--timeout SEC` | 1 回の問い合わせを待つ秒数 | 60 |
| `--max-workers N` | 同時に送る問い合わせの数。AAP の負荷を下げたいときは小さくする | 4 |
| `--request-interval SEC` | 問い合わせの最小間隔の秒数。AAP の負荷をさらに下げたいときに使う | 0 |

</details>

<details>
<summary>バージョンと終了コード</summary>

`python3 -m aapstats --version` で、ツールのバージョンを表示します。

シェルスクリプトや cron で結果を判定できるよう、次の終了コードを返します。

| 終了コード | 意味 |
|---|---|
| 0 | 正常に終わった |
| 1 | 接続または認証のエラー（接続できない、トークンが正しくない、権限がないなど） |
| 2 | 引数や設定の誤り（存在しないプロファイル、存在しない列、日付の書き方の誤りなど） |
| 130 | Ctrl+C で中断した |

</details>

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

各オプションの説明は、次の折りたたみにあります。日付は `2026-08-01` のように書きます。日付だけを書くと、`--tz`（既定: Asia/Tokyo）のその日の 0 時を指します。`2026-08-01T09:00:00` のように時刻まで書くこともできます。

<details>
<summary>共通のオプション（すべてのリソース）</summary>

**`--org NAME`**

組織の名前で絞り込みます。名前は完全に一致するものだけを出します。複数の組織を出すときは、`--org A --org B` のように繰り返します。組織に属さない認証情報（組織が空欄のもの）は出ません。`organizations`、`credential_types`、`authenticators` では使えません。

```
python3 -m aapstats list job_templates --org Default
```

**`--name TEXT`**

名前に `TEXT` を含むものを出します。大文字と小文字は区別しません。たとえば `--name NOTE` は `write-note-linux` に一致します。

```
python3 -m aapstats list job_templates --name note
```

**`--name-regex REGEX`**

名前が正規表現に一致するものを出します。名前の一部に一致すれば対象になり、大文字と小文字を区別します。名前の先頭から一致させるときは `^` を、末尾までなら `$` を付けます。

```
python3 -m aapstats list job_templates --name-regex '^write-'
```

**`--created-before DATE` / `--created-after DATE`**

作成日時で絞り込みます。`--created-before` は指定した日時**より前**、`--created-after` は指定した日時**以降**に作成されたものを出します。2 つを組み合わせると、期間を指定できます。

```
# 2026 年 11 月に作成されたもの
python3 -m aapstats list job_templates --created-after 2026-11-01 --created-before 2026-12-01
```

**`--modified-before DATE` / `--modified-after DATE`**

更新日時で絞り込みます。考え方は `--created-before` / `--created-after` と同じです。長いあいだ手が入っていないリソースを探すときに使います。

```
# 2026 年 1 月より前から更新されていないプロジェクト
python3 -m aapstats list projects --modified-before 2026-01-01
```

**`--sort COLUMN`**

指定した列で並べ替えます。指定できる列は、`--wide` で表示される列（テンプレートでは `run_count` も）です。降順にするときは、列名の前に `-` を付け、`--sort=-列名` のように `=` でつなぎます。`=` を省くと、`-` で始まる値がオプションと見なされてエラーになります。値が空のものは、昇順でも降順でも最後に並びます。

```
# 最終実行日時の新しい順
python3 -m aapstats list job_templates --sort=-last_job_run
```

**`--limit N`**

出す件数の上限です。並べ替えと絞り込みを済ませたあとで、先頭から `N` 件を出します。

```
python3 -m aapstats list job_templates --sort=-last_job_run --limit 10
```

</details>

<details>
<summary>利用状況のオプション（job_templates と workflow_job_templates）</summary>

**`--unused-days N`**

`N` 日以上実行されていないテンプレートを出します。一度も実行されていないものも含みます。判定には、テンプレートの最終実行日時（`last_job_run`）を使います。`last_job_run` は、AAP 側で古いジョブ履歴が削除されても残るため、履歴の保持期間（初期値は 120 日）より長い日数も指定できます。AAP に残っている履歴の日数より長い日数を指定すると、その旨の警告が出ます（判定は `last_job_run` で正しく行われます）。

```
python3 -m aapstats list job_templates --unused-days 90
```

**`--never-run`**

一度も実行されていないテンプレートを出します。

```
python3 -m aapstats list job_templates --never-run
```

**`--last-status STATUS`**

最後の実行結果で絞り込みます。`STATUS` には次のいずれかを指定します。

| 値 | 意味 |
|---|---|
| `successful` | 最後の実行が成功した |
| `failed` | 最後の実行が失敗した |
| `error` | 最後の実行がエラーで終わった |
| `canceled` | 最後の実行がキャンセルされた |
| `never` | 一度も実行されていない |

```
python3 -m aapstats list job_templates --last-status failed
```

**`--min-runs N` / `--max-runs N`**

期間内の実行回数で絞り込みます。`--min-runs` は `N` 回以上、`--max-runs` は `N` 回以下のものを出します。期間は `--since` で指定します。結果には `run_count`（期間内の実行回数）の列が加わります。

実行回数は、テンプレートごとに AAP の API に問い合わせて数えます。テンプレートが多いと時間がかかるため、ほかの絞り込みを先に済ませてから、残ったテンプレートだけを数えます。

```
# 8 月以降に 1 回も実行されていないテンプレート
python3 -m aapstats list job_templates --since 2026-08-01 --max-runs 0

# 8 月以降に 10 回以上実行されたテンプレート
python3 -m aapstats list job_templates --since 2026-08-01 --min-runs 10
```

**`--since DATE`**

実行回数（`run_count`）を数える期間の始まりです。指定しなければ、AAP に残っている履歴すべてで数えます。`--since` だけでは絞り込みません。`--min-runs`、`--max-runs`、`--columns run_count` と組み合わせて使います。保持期間より前の日付を指定すると、削除済みの分だけ回数が少なく出る旨の警告が出ます。

```
python3 -m aapstats list job_templates --since 2026-08-01 --columns name,run_count --sort=-run_count
```

</details>

<details>
<summary>参照関係のオプション</summary>

**`--orphan`**（job_templates と workflow_job_templates）

有効なスケジュールからも、ワークフローからも呼ばれていないテンプレートを出します。無効なスケジュールしかないものも対象です。手で実行する以外に起動される経路がないテンプレートを探すときに使います。

```
python3 -m aapstats list job_templates --unused-days 90 --orphan
```

**`--no-schedule`**（job_templates と workflow_job_templates）

スケジュールが 1 つもないテンプレートを出します。`--orphan` と違い、無効なスケジュールがあるものは対象外です。

```
python3 -m aapstats list job_templates --no-schedule
```

**`--unreferenced`**（projects、inventories、credentials、execution_environments）

どこからも使われていないリソースを出します。AAP の組み込みのリソースは除き、除いたものの名前をメッセージで知らせます。「使われている」とみなす参照は、リソースごとに次のとおりです。

| リソース | 数える参照 |
|---|---|
| `projects` | テンプレート、インベントリソース（ソースのプロジェクト）、ワークフローの同期ノード |
| `inventories` | テンプレート、ワークフローのインベントリ同期ノード |
| `credentials` | テンプレート、プロジェクト（SCM 認証情報）、インベントリソース、組織（Galaxy 認証情報） |
| `execution_environments` | テンプレート、プロジェクトの既定、組織の既定、インベントリソース |

```
python3 -m aapstats list credentials --unreferenced
```

</details>

<details>
<summary>逆引きのオプション（job_templates と workflow_job_templates）</summary>

指定したリソースを使っているテンプレートを出します。移行の対象から外そうとしているリソースを、どのテンプレートが使っているかを確かめるときに使います。

値には、リソースの名前か ID を指定します。数字だけを指定すると ID として扱います。名前は完全に一致するものだけを対象にします。

| オプション | 探すもの |
|---|---|
| `--uses-project NAME\|ID` | このプロジェクトを使うジョブテンプレート |
| `--uses-inventory NAME\|ID` | このインベントリを使うジョブテンプレートとワークフロー |
| `--uses-credential NAME\|ID` | この認証情報を割り当てたジョブテンプレート（Webhook の認証情報は対象外） |
| `--uses-ee NAME\|ID` | この実行環境を指定したジョブテンプレート（実行環境を指定せず既定で動くものは対象外） |

ワークフローは、プロジェクト、認証情報、実行環境を直接は使わないため、`workflow_job_templates` に `--uses-project`、`--uses-credential`、`--uses-ee` を指定すると 0 件になります。

```
python3 -m aapstats list job_templates --uses-credential linux-ssh
python3 -m aapstats list job_templates --uses-ee 5
```

</details>

<details>
<summary>列の選び方（--columns、--wide）</summary>

**`--columns COLS`**

出す列を、カンマ区切りで指定した順に出します。存在しない列を指定すると、使える列の一覧を示すエラーになります。

```
python3 -m aapstats list projects --columns name,scm_url,used_by_templates
```

**`--wide`**

すべての列を出します。`--format json` のときは、`--columns` を指定しない限り、もともとすべての列を出します。

```
python3 -m aapstats list credentials --wide --format csv -o credentials.csv
```

</details>

### deps

ジョブテンプレートまたはワークフローを 1 つ指定し、移行先で動かすために一緒に移行が必要なリソースを出します。

```
python3 -m aapstats deps workflow_job_template "月次パッチ適用"
python3 -m aapstats deps jt 19 --format csv -o deps.csv
```

- 表示形式が `table` のときは、ツリーと、種類ごとにまとめた一覧を出します。同じリソースが 2 回目以降に出てきたときは、中身を展開せず「上に展開済み」と表示します。
- カスタムの認証情報タイプには「移行先で作り直しが必要」と注記します。
- `csv` は種類ごとにまとめた一覧だけを、`json` はツリーと一覧の両方を出します。

<details>
<summary>指定できるリソースの書き方</summary>

1 つ目の引数にはリソースの種類を、2 つ目にはテンプレートの名前か ID を指定します。数字だけを指定すると ID として扱います。同じ名前のテンプレートが複数あるときは、候補の ID を示すエラーになるので、ID で指定し直します。

| 種類 | 書き方（どれでも同じ） |
|---|---|
| ジョブテンプレート | `job_template`、`job_templates`、`jt` |
| ワークフロー | `workflow_job_template`、`workflow_job_templates`、`wfjt` |

</details>

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

<details>
<summary>オプションの説明</summary>

**`--kinds KINDS`**

取得する履歴の種類を、カンマ区切りで指定します。既定は `job,workflow_job` です。プロジェクトやインベントリの同期の統計も見たいときに、`project_update` や `inventory_update` を加えます。

| 値 | 取得する履歴 |
|---|---|
| `job` | ジョブテンプレートの実行 |
| `workflow_job` | ワークフローの実行 |
| `project_update` | プロジェクトの同期 |
| `inventory_update` | インベントリの同期 |

**`--since DATE`**

初回の取得で、この日以降の履歴だけを取得します。履歴が多く、初回の取得に時間がかかるときに使います。2 回目以降の取得では使われません（前回の続きから取得するため）。

**`--db PATH`**

保存先の SQLite ファイルを指定します。省略すると `~/.local/share/aapstats/<プロファイル名>.sqlite` に保存します。`stats` でも同じファイルを指定します。

</details>

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

#### ワークフローを集計する

`stats` は、ジョブテンプレートとワークフローの両方を集計します。結果の `template_type` 列が `workflow` の行がワークフローです。ワークフローの行にも、実行回数、成功率、実行時間、起動方法の内訳などが出ます。

ワークフローだけを集計するときは、`--kinds workflow_job` を付けます。

```
# ワークフローだけのテンプレート別の統計
python3 -m aapstats --profile source stats --kinds workflow_job

# 実行回数が多いワークフローの上位 10 件
python3 -m aapstats --profile source stats --kinds workflow_job --top 10 --by runs

# ワークフローの月別の実行回数
python3 -m aapstats --profile source stats --kinds workflow_job --group-by month
```

- ワークフローの実行時間は、中で動いたジョブを含めた、始まりから終わりまでの時間です。
- ジョブテンプレートの行の `launch_workflow` 列は、そのテンプレートがワークフローから起動された回数です。手動やスケジュールでは使われず、ワークフローの部品としてだけ動いているテンプレートを見分けるときに使います。
- `--kinds job` を付けると、ジョブテンプレートだけを集計します。

<details>
<summary>オプションの説明</summary>

**`--since DATE` / `--until DATE`**

集計する期間です。`--since` は指定した日時**以降**、`--until` は指定した日時**より前**に始まったジョブを集計します。省略すると、保存済みの履歴すべてを集計します。保存済みの履歴より前の日付を指定すると、その旨の警告が出ます。

**`--org NAME`**

組織の名前で絞り込みます。名前は完全に一致するものだけを対象にします。複数の組織は、`--org A --org B` のように繰り返して指定します。

**`--name TEXT`**

テンプレートの名前に `TEXT` を含むジョブだけを集計します。大文字と小文字は区別しません。

**`--group-by KEY`**

集計の単位を変えます。省略すると `template`（テンプレートごと）です。

| 値 | 集計の単位 |
|---|---|
| `template` | テンプレート（既定） |
| `organization` / `project` / `inventory` / `execution_environment` | 組織 / プロジェクト / インベントリ / 実行環境 |
| `credential` | 認証情報。1 つのジョブが複数の認証情報を使ったときは、それぞれに 1 回ずつ数える |
| `day` / `week` / `month` | 日 / 週（月曜始まり） / 月 |
| `hour` / `weekday` | 時間帯（0〜23 時） / 曜日。移行先の処理能力を見積もるときに使う |

**`--top N` / `--by KEY` / `--min-runs N`**

`--top N` で、上位 N 件のランキングを出します。既定では次の 3 つを出し、`--by` で 1 つに絞れます。`--group-by` とは同時に使えません。

| `--by` の値 | ランキング |
|---|---|
| `runs` | 実行回数が多いテンプレート |
| `failure_rate` | 失敗率（失敗とエラーの割合）が高いテンプレート |
| `avg_elapsed` | 平均実行時間が長いテンプレート |

`--min-runs N` で、実行回数が N 回未満のテンプレートをランキングから除きます（既定は 1）。1 回しか動いていないテンプレートが失敗率 100% で上位に並ぶのを避けるときに使います。

**`--wide`**

テンプレートごとの統計で、すべての列（最大実行時間、合計実行時間、再実行・コールバック・Webhook での起動回数など）を出します。`--format json` のときは、もともとすべての列を出します。

**`--kinds KINDS`**

集計する履歴の種類です。既定は `job,workflow_job` です。値は `sync` の `--kinds` と同じです。`sync` で取得していない種類は集計できません。

**`--db PATH`**

集計する SQLite ファイルを指定します。省略すると、`sync` と同じ既定の場所（`~/.local/share/aapstats/<プロファイル名>.sqlite`）を読みます。`--db` を指定すれば、プロファイルを指定しなくても動きます。

</details>

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
