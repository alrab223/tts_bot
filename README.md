# Discord読み上げBotの導入と移行

`develop_gekiatsu`から音声読み上げを独立させたプロジェクトです。このフォルダだけを別の場所にコピーして運用できます。既存Botのコードとデータは変更しません。

DockerにはPython、uv、FFmpeg、Opus、libsodiumとPython依存をまとめています。新しいホスト側で音声用aptパッケージやPythonを個別に導入する必要はありません。まずDiscordのBotトークンを設定し、コンテナを起動してVCへ召喚してください。

## 含まれる機能

テキストチャンネルの自動読み上げ、任意テキストの朗読、VCへの召喚・退出、再生停止、再起動後のVC再接続を提供します。音声合成にはgTTSを使います。辞書、読み上げ文中のSE、既存の入室SE設定も移行できます。

設定・再生キュー・VC接続はDiscordサーバーごとに分離しています。読み上げ先は召喚したチャンネルから決まるため、環境変数にサーバーIDやVCのIDを列挙する必要はありません。読み上げは最大100文字、待ち行列はサーバーごとに32件が初期値です。

リマインダー、画像編集、絵文字、一般通知、Discordサウンドボードの登録・直接再生などは含めません。読み上げ文中のSEは順番に再生するため、従来の音声結合による`ffmpeg-python`依存も不要です。

## 動作に必要なもの

- LinuxのDocker EngineとCompose v2、またはWindows／macOSのDocker Desktop。WindowsではLinuxコンテナを使用します。
- Discord Developer Portalで作成したBotトークン。既存Botと並行運用する場合は別のBotアプリを用意します。
- Discordと、音声合成に使うGoogleへのインターネット接続。VCの送信にはUDP通信も必要です。

gTTSの音声合成には外部通信が必要です。Googleに接続できなければ読み上げは失敗します。

Discord Developer PortalのBot設定で **Message Content Intent** を有効にします。招待用OAuth2 scopesは`bot`と`applications.commands`、権限は「チャンネルを見る」「接続」「発言」を付けます。通常の読み上げに管理者権限は不要です。[discord.pyの導入説明](https://discordpy.readthedocs.io/en/stable/intro.html#installing)も参照してください。

## 新しい環境でDocker起動

GitHubから取得する場合は、リポジトリへのアクセス権があるアカウントで認証し、次を実行します。

```bash
git clone https://github.com/alrab223/tts_bot.git
cd tts_bot
```

以下のコマンドは、取得またはコピーした`tts_bot`フォルダ内で実行します。
この節は設定を新規作成する場合の手順です。複製済みの既存設定を使う場合は、`.env`を設定した後、「Dockerのデータ領域へ復元」の手順へ進んでください。

Linux／macOS:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

`.env`の`BOT_TOKEN=`にトークンを記入します。この1項目の設定で開始できます。

```bash
docker compose build
docker compose run --rm --no-deps tts-bot uv run --frozen --no-sync python main.py --check
docker compose up -d
docker compose logs -f --tail=100
```

`--check`はDiscordへログインせずに、Pythonパッケージ、Opusのロード、FFmpegによる無音PCMの生成、データ保存先を確認します。既存設定を復元する場合は、空のデータ領域が必要なので、この検証・Bot起動より先に次節の復元を実行してください。
Dockerのビルド中にも一時的な保存先で同じ検証を行い、音声用ライブラリが使えなければビルドを失敗させます。

DiscordでVCに参加し、読み上げたいテキストチャンネルで`/botをボイスチャンネルに召喚`を実行します。このチャンネルの読み上げが自動で有効になります。`/朗読`で短い文章を試します。

コマンドのグローバル登録はDiscordへの反映に時間がかかる場合があります。開発時にすぐ確認したい場合だけ`.env`の`COMMAND_GUILD_ID`にテスト先のDiscordサーバーIDを設定して起動します。この値はコマンドの登録先であり、読み上げ先チャンネルの指定ではありません。運用時は空に戻し、再起動してください。

## 既存Botから設定と音声を移行

### 読み上げ用データの書き出し

GitHubのリポジトリには既存設定や音声を含めません。ローカルの`data/`に書き出し済みの設定がある場合は再書き出し不要です。移行結果と不足している参照音声のパスは`data/migration-report.json`で確認できます。既存データを引き継ぐ場合は、`data/`を別途コピーしてください。

現在のBotのルート直下にある`tts_bot`から、uvで実行します。元Botは稼働したままでも、移行スクリプトはSQLiteを読み取り専用で開きます。移行先のBotは停止しておきます。

```bash
uv sync --locked --no-install-project
uv run --frozen --no-sync python scripts/migrate_legacy.py --source ..
```

`data/speech.sqlite3`と`data/audio/`に、次だけを複製します。

- `read_text_se`のトリガー・音量・優先度と、参照されているSEファイル
- `enter_se`のユーザー別入室SE設定と、その音声ファイル

元の絶対パスは、移行先の`data/audio`に対する相対パスへ置き換えます。参照音声が存在しないものは件数を表示してスキップします。生成済みの読み上げ音声、元の`.env`、Google認証情報、リマインダー、画像編集台帳はコピーしません。
同じユーザーの入室SEが元DBに重複する場合は、既存Botと同じ先頭行を採用します。同じトリガーのSEが複数ある場合は優先度順の先頭を採用します。

既存データがある移行先は保護します。同じ単語・ユーザーIDの設定を更新する場合だけ`--merge`を付けます。別の空フォルダへ出力するには`--destination /path/to/exported-data`を使います。

```bash
uv run --frozen --no-sync python scripts/migrate_legacy.py --source .. --destination ./exported-data
```

読み上げのオンオフ状態も継承する場合は`--guild-id <移行先DiscordサーバーID>`を付けます。同じDiscordサーバーの旧読み上げチャンネルIDまで継承する場合だけ、さらに`--keep-channels`を付けます。別のDiscordサーバーへ移す場合はこのオプションを使いません。VCの接続先は新しいBotを召喚して設定します。

### Dockerのデータ領域へ復元

新環境へ`tts_bot`フォルダと、書き出した`data`フォルダをコピーし、`.env`を設定してから実行します。`data`はDockerのビルド対象に入らないため、明示的にnamed volumeへ復元します。

```bash
docker compose build
docker compose run --rm --no-deps --volume ./data:/import:ro tts-bot uv run --frozen --no-sync python scripts/restore_data.py
docker compose up -d
```

復元は空のnamed volumeに対して一度だけ実行できます。既存データを自動削除しません。復元スクリプトはSQLiteのbackup APIを使い、移行元にWALが残っている場合も確定済みトランザクションを取り込みます。別の書き出し先を使った場合は`./data`をそのパスに置き換えます。

## 辞書の設定

### ローカルCSV

初回起動時に`data/dictionary.csv`を作ります。形式は次のとおりです。優先度の小さいものから置換します。

```csv
word,reading,priority
Discord,ディスコード,1
uv,ユーブイ,10
```

DockerではCSVをホストで編集し、次のようにコピーしてから`/読み上げ辞書更新`を実行します。初期設定の時点では、復元する`data`フォルダへCSVを含めても構いません。

```bash
docker compose cp ./dictionary.csv tts-bot:/app/data/dictionary.csv
```

### 現在のGoogle Sheets辞書を引き続き使用

既存のサービスアカウントJSONを`secrets/service_account.json`へ手動でコピーし、対象のスプレッドシートをそのアカウントに共有します。JSONをGitやDockerイメージへ含めない構成です。Googleの辞書内容は移行スクリプトでは取得しません。

`.env`に`GOOGLE_SHEET_ID=<スプレッドシートID>`を設定します。現在と同じシート名で開く場合は、代わりに`GOOGLE_SHEET_NAME=読み上げ`を使えます。先頭シートの列順を「単語、読み、優先度」にします。

```bash
docker compose -f compose.yaml -f compose.google.yaml up -d --build
```

以降の停止・更新操作も同じ2つの`-f`を指定します。Googleへの接続失敗時は起動を継続し、置換なしの読み上げになります。実行中の辞書更新が失敗した場合は、直前の辞書を維持します。
Linuxでは、コンテナのUID 10001がサービスアカウントJSONを読み取れる所有権・グループ権限にしてください。

## コマンド一覧

| コマンド | 動作 |
|---|---|
| `/botをボイスチャンネルに召喚` | 実行者のVCへ接続し、実行チャンネルの読み上げを開始 |
| `/botをボイスチャットから追い出す` | 再生待ちを破棄し、VCから退出 |
| `/朗読 text:` | 指定した文章を読み上げ |
| `/読み上げ停止` | 現在の音声と再生待ちを停止 |
| `/読み上げ機能のオンオフ` | 自動読み上げの切替。オンにしたチャンネルを対象にする |
| `/読み上げ辞書更新` | CSVまたはGoogle Sheetsから辞書を再取得 |
| `/se追加` | 単語に対応するSEを音声添付で登録 |

SEの`short=1`が文中再生の対象です。`short=0`は旧設定互換のため保存しますが、文中では再生しません。SE設定はプロジェクト内で共通、VC・読み上げ先・オンオフ・再生待ちはサーバーごとです。

## OSパッケージと再現性

| 役割 | 元のホスト | DockerのDebian Bookworm |
|---|---|---|
| 音声のデコード・PCM変換 | ffmpeg 4.1.11とlibavcodec58等 | ffmpegと、その依存するlibavcodec・libavformat・libavutil・libswresample |
| Discordの音声エンコード | libopus0 | libopus0 |
| 音声暗号化 | libsodium23・PyNaCl | libsodium23・PyNaCl |
| CFFIの実行時ABI | libffi6 | libffi8 |
| HTTPSの証明書 | ca-certificates | ca-certificates |
| ビルド時のヘッダー | libffi-dev・libsodium-dev | wheelだけをインストールするため不要 |

元ホストで確認したバージョンは`docs/host-apt-packages.tsv`に記録しています。OSの世代とCPUが異なる環境へBuster用のABIパッケージをそのまま持ち込まず、Docker内でBookworm用の実行ライブラリを導入します。FFmpegのPythonラッパーは実行ファイルの代わりにはならないため、Dockerfileで`ffmpeg`をapt導入しています。

Pythonは3.11.11、uvは0.6.3、直接・間接Python依存は`uv.lock`で固定しています。PyNaClはdiscord.pyの`voice`追加依存の指定範囲に合わせ1.5.0を選択しています。`uv sync --locked`はロックファイルの更新が必要なら失敗します。[uv公式のDocker導入手順](https://docs.astral.sh/uv/guides/integration/docker/)に沿ってuvをイメージへ組み込んでいます。

aptリポジトリの更新により、同じDockerfileでも将来の再ビルドではOSパッケージのパッチ版が変わります。完全に同じ実行環境を持ち運ぶ場合は、ビルド済みイメージを保存・転送してください。同じCPUアーキテクチャの移行先では再ビルド不要です。

```bash
docker image save -o gekiatsu-tts-1.0.0.tar gekiatsu-tts:1.0.0
# 移行先で実行
docker image load -i gekiatsu-tts-1.0.0.tar
docker compose up -d --no-build
```

arm64のラズパイとamd64のPCをまたぐ場合は、移行先でビルドするか、各アーキテクチャ向けのイメージを用意します。対象は64bitのLinux arm64／amd64です。音声再生にホストのサウンドカードや`/dev/snd`のマウント、受信用ポートの公開は不要です。

イメージ内で実際に導入されたaptパッケージの一覧は次で取得できます。

```bash
docker compose run --rm --no-deps tts-bot cat /usr/local/share/tts-os-packages.txt
```

## 停止と更新とバックアップ

旧バージョンから更新した場合は、起動時に廃止された音声合成設定をDBから除去します。読み上げ先・VC接続先・SE・辞書は保持します。

```bash
docker compose ps
docker compose logs --tail=100
docker compose stop
docker compose up -d --build
```

設定・SE・辞書はnamed volumeの`gekiatsu-tts_tts-data`に保存し、コンテナを作り直しても維持します。`docker compose down`はデータを残します。`down -v`はデータを削除するため、通常の更新には使いません。

ホストへバックアップする場合は、Botを停止してから取得します。

```bash
docker compose stop
docker compose cp tts-bot:/app/data ./backup-data
docker compose up -d
```

`backup-data`は新環境での初回復元にも使えます。`.env`とGoogleサービスアカウントJSONは別管理です。`data/cache`の生成音声は再生終了時に削除し、移行時の復元対象にも含めません。

コンテナはUID 10001の一般ユーザーとして動作し、書き込み先は`/app/data`と一時領域だけです。ホストの`./data`を直接bind mountする構成へ変更する場合は、UID 10001が書き込める権限を設定してください。起動後のヘルスチェックはDiscord接続中に更新するheartbeatを見ます。`unhealthy`だけではDockerは自動再起動しないため、まずログとネットワークを確認します。

## Dockerを使わずuvで起動

Linuxでは以下を導入します。Debian Bookwormを想定したパッケージ名です。

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg libopus0 libsodium23 libffi8 ca-certificates
uv sync --locked --no-install-project
uv run --frozen --no-sync python main.py --check
uv run --frozen --no-sync python main.py
```

Windowsでネイティブ起動する場合はFFmpegとOpus DLLの導入が別途必要です。OS依存まで含めて揃える用途ではDocker DesktopのLinuxコンテナを使用してください。

## コードの整形と静的チェック

Pythonコードはインデント3スペースの既存規約を維持し、行長79文字で統一します。
開発用ツールは`uv.lock`に固定し、Dockerの本番イメージには含めません。

```bash
uv sync --locked --no-install-project
uv run --frozen --no-sync ruff format .
uv run --frozen --no-sync ruff check .
uv run --frozen --no-sync pycodestyle --config=setup.cfg .
```

変更を加えず整形状態だけを確認する場合は`ruff format --check .`を使います。
`setup.cfg`、`.editorconfig`、VS CodeのPython設定も3スペースに合わせています。

## 検証

Discordへログインせずに行う自動テスト:

```bash
uv sync --locked --no-install-project
uv run --frozen --no-sync python -m unittest discover -s tests -v
uv lock --check
```

実運用の確認は、専用Botを招待したテストサーバーで、召喚→通常投稿→朗読→停止→退出を試します。元のBotと同じトークンで同時起動しないでください。

### 作成時の検証結果

2026年10月20日時点で、自動テスト20件、uvのロック整合性、Compose設定の構文、ホスト上でのFFmpeg実行・PCM生成・Opusエンコードを確認しました。Pythonベースイメージのレジストリ情報にはamd64・arm64が含まれています。

この作業環境ではDockerデーモンへのアクセス権限がなく、イメージの実ビルドとコンテナ起動は未検証です。上記の初回ビルドと`--check`を、Dockerを操作できるホストで実行してください。実Discordでの接続・読み上げ試験も、そのホスト上で行います。
