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

## テスト

```
python3 -m unittest discover -s tests -t .
```
