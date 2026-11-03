# Prompt Ledger

ローカル専用のプロンプト整理アプリ。ユーザーが選んだ PNG / JPEG / JSON の
**メタデータだけ**を解析し、Positive と Negative を Qwen Embedding で別々に比較します。
採用したペアだけを JSON に書き出します。日本語 UI、CLI、JSON API は同じ判定コアを使います。

画像プレビュー、画像認識、画像生成、外部推論 API はありません。
元画像を変更・削除・保存しません。過去フォルダを自動で検索しません。
複数の**除外文字列**を保存できます。Positive / Negativeへのリテラル部分一致で、
大小文字を区別しません（Unicode casefold）。一致したペアは推論前に除外し、
既存の採用済みペアにも書き出し時の現行ルールを適用します。初期リストは空です。
UIの「本文を表示」をONにすると編集できます。詳細・CLI/API例は [docs/EXCLUSIONS.md](docs/EXCLUSIONS.md)。
ブラウザから選択したファイルはループバックへ一時送信され、メモリ上でメタデータだけを読みます。
プロンプトとベクトルはローカル SQLite に残ります。PC上の他ユーザー向けの認証/暗号化システムではありません。

## Quick start / Windows

### GitHub からダウンロードして試す

1. リポジトリの閲覧権限がある GitHub アカウントでログインします。
2. リポジトリ画面の **Code → Download ZIP** を選び、ダウンロードしたZIPを
   エクスプローラーの「すべて展開」で展開します。ZIP内から直接実行しないでください。
3. 展開したフォルダ内の `README.md` と `pyproject.toml` がある場所で
   PowerShellを開きます。フォルダ名に空白や日本語があっても利用できます。
4. Python と uv を下記の公式手順で導入済みにして、次のコマンドを実行します。
   `uv sync` がこのフォルダ専用の `.venv` を作成します。手動のactivateは不要です。

Python: [公式Windowsダウンロード](https://www.python.org/downloads/windows/)。
uv: [公式インストール手順](https://docs.astral.sh/uv/getting-started/installation/)。
既存の別アプリの仮想環境にはインストールしないでください。

Python 3.10–3.13 と [uv](https://docs.astral.sh/uv/) を使います。
リポジトリのルートで実行してください。既存の環境は変更せず、専用 `.venv` を使います。

```powershell
uv sync --frozen
uv run --frozen prompt-ledger --root . serve
```

`http://127.0.0.1:8765` を開きます。モデルなしでも UI とメタデータ確認を利用できます。
`localhost` の別名ではなく **127.0.0.1** を使ってください。

### 次回からは start.bat

初回は **setup.bat をダブルクリック**する方法も利用できます。
Python 3.10～3.13を事前に公式サイトから導入してください。
スクリプトの配置場所を基準に専用 `.venv` を作成し、固定版uv 0.11.7を
`.cache/setup-tools` に、`uv.lock`のCPUモデル用依存まで `.venv` に導入します。
公式PyPIへの通信が発生します。既存環境の不要パッケージやデータは削除しません。
再実行可能で、既存 `.venv` が不完全/非対応なら自動削除せずエラーで停止します。
続けて公式Qwenモデルの固定revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` を取得・整合性確認します。
成功後は `start.bat` からモデルを利用できます。セットアップ中に推論やGPU起動は行いません。

通常のダブルクリックでモデル取得まで実行します。基本依存だけに限定する場合は
`setup.bat --skip-model` を明示してください。別の公式revisionを選ぶ場合のみ次を使います。
2026-11-03に固定revisionの公式ファイルメタデータを取得し、モデル対象合計は
**1,207,470,234 bytes** と確認しました。初回のモデル作業用空き容量目安は
**2,683,375,924 bytes**（残り取得量の2倍＋256MiB）です。
CPU依存やuvの通信・容量は別途必要です。実行時にも取得対象の実サイズ、残量、空き容量を表示します。
公式PyPI・PyTorch CPU配布元・Hugging Faceに接続します。

```powershell
.\setup.bat --model-revision REPLACE_WITH_OFFICIAL_40_CHARACTER_COMMIT_SHA
```

同じrevisionの正常済みファイルはハッシュ検証後に再利用し、途中取得は再実行で再開します。
セットアップのたびに公式固定revisionのメタデータへ接続し、ファイル一覧・サイズ・ハッシュを
照合します。ローカルmanifestや再開情報だけを公式由来の証明として信用しません。
公式メタデータを取得できなければ成功扱いにせず停止します（推論時は従来どおりオフライン）。
空・形式不正の再開情報は明示エラーとなり、自動削除や成功manifest作成は行いません。
破損が確認された管理対象ファイルだけ再取得し、全ファイルの公式ハッシュ一致後に
manifestをatomic保存します。別revisionや管理情報のない既存ファイルは変更せず停止します。
強制終了で `.setup.lock` が残った場合は、他のsetupが動いていないことを確認してから
モデルフォルダ内のこのロックファイルだけを手動で取り除いてください。
証明書エラーでのSSL無効化・迂回は行いません。
`setup.bat --check` はPython/パス確認だけで通信・環境作成なし。
終了コードは0=成功、2=Python/入力/環境不備、1=導入失敗、130=中断です。

初回の `uv sync --frozen` 後は、展開フォルダの **start.bat をダブルクリック**すると
そのフォルダの `.venv` で起動します。作業ディレクトリや `LEDGER_ROOT` に依存しません。
表示された `http://127.0.0.1:8765` をブラウザで開いてください。
環境未作成・必須依存不足は日本語の案内を表示して終了します。
モデル未取得・モデル用依存不足は案内を表示し、画面と解析のみを利用できます。
インストール・重いダウンロード・証明書設定変更は自動実行しません。
起動失敗時はウィンドウを閉じずにエラーを確認でき、終了は `Ctrl+C` です。

コマンドから `start.bat --check` で依存と設定だけを確認できます。
DB作成・サーバー起動・推論は行いません。終了コードは0が確認完了、2が必須依存不足、
1が起動/設定エラーです。モデル未準備の案内だけでは失敗扱いにしません。
移動した `.venv` が壊れた場合はREADMEの手順で専用環境を再作成してください。

### Qwen model setup（明示的ダウンロード）

**現在の検証状況:** 以前は公式Hugging Faceへの取得が
`CERTIFICATE_VERIFY_FAILED: self signed certificate in certificate chain` で停止しましたが、
2026-11-03の再接続後、TLS設定を変更せず公式固定revisionの10ファイル取得と
公式ハッシュ検証に成功しました。再実行時のSHA-256検証・取得スキップも確認済みです。
実データでの意味類似度の品質や長文性能は未検証です。
短い合成テキスト3件の実Qwen CPU推論は成功しました（GPU不使用、2 threads、
1024次元、有限値、L2 norm確認）。初回ロード込み約26秒、同一ペア再利用と
negative変更時の別採用を確認しました。これは実データ全体での品質保証ではありません。
合成ベクトルによるテスト合格は実モデルの品質保証ではありません。
重みがなくても画面、除外ルール編集、解析のみの確認を試せますが、通常の意味類似度判定には重みが必要です。

証明書エラー時は処理を止め、PCの日時と組織のネットワーク方針を確認し、必要なら
管理者へ正規の証明書チェーンを相談してください。TLS検証の無効化、`--insecure`、
プロキシの迂回、認証情報の追加で回避しないでください。
このアプリは証明書やセキュリティ設定を変更しません。

```powershell
uv sync --frozen --extra model
# 公式モデルの Files and versions / commit history で40桁のcommit SHAを確認して代入
$revision = "REPLACE_WITH_OFFICIAL_40_CHARACTER_COMMIT_SHA"
uv run --frozen --extra model prompt-ledger --root . setup-model --revision $revision
uv run --frozen --extra model prompt-ledger --root . serve
```

公式ソース: [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/main)。
ブランチ名 `main` を固定版として保存することはありません。revision は必須です。
モデルは `models/qwen3-embedding-0.6b/` にだけ取得します。
配置は `setup-model` に任せてください。固定revisionのファイルと整合性確認用
`manifest.json` を作成するため、重み1ファイルを手動で置くだけでは起動できません。
モデルは本体ZIPに同梱しません。QwenモデルはApache-2.0で別途提供されます。
setup-model はネットワークを使いますが、入力画像・プロンプトを送信しません。
推論は `local_files_only=True`、`trust_remote_code=False`、CPU、float32、2 threads。
CUDA / GPU を使用せず、既存モデルを unload しません。重みは自動取得しません。
初回ロード時に取得ファイルの SHA-256 を確認します。
同一revisionの管理対象ファイルは検証して再開・修復します。別revisionや管理情報のない既存ファイルは変更せず停止します。

### Linux

```sh
uv sync --frozen --extra model
REVISION=REPLACE_WITH_OFFICIAL_40_CHARACTER_COMMIT_SHA
uv run --frozen --extra model prompt-ledger --root . setup-model --revision "$REVISION"
uv run --frozen --extra model prompt-ledger --root . serve
```

CPU wheels は公式 PyTorch CPU index、他の依存は公式 PyPI から解決します。
`uv.lock` は Python/OS ごとのバージョン選択とハッシュを固定します。
再配布はこのソース＋lockを対象にします。モデルや `.venv` の配布は対象外です。

### 起動後：除外設定から JSON 出力まで

1. ブラウザで `http://127.0.0.1:8765` を開きます。
2. 「本文を表示」をONにして、除外文字列を1行に1つ入力し保存します。
   初期値は空です。正規表現ではなく大小文字を区別しない部分一致です。
3. 自分で対象ファイルを選択し、まず解析のみ、必要に応じてdry-runで確認します。
   除外対象はQwen推論前に除外します。解析のみは除外判定やDB保存を行いません。
4. モデル準備後に取り込み、採用・重複・要確認・除外を確認します。
   要確認は本文を確認して補正できます。画像プレビューはありません。
5. JSON書き出しを選びます。現行の除外ルールに一致する既存採用データも出力されません。
   元の記録は削除されません。ルール変更前に保存済みのJSONは自動更新されません。
6. 終了はサーバーのPowerShellで `Ctrl+C`。次回も同じフォルダからserveを実行します。

補正した内容が除外された場合、元の要確認は未解決のまま残ります。
ルール解除後に同じ要確認の「確認・補正」から再評価してください。
元ファイルが解析不能な場合も、この操作で再評価できます。除外履歴は削除しません。

## 判定規則

1. positive は空でない文字列、negative は明示的な文字列（空文字も可）。
2. 同一ペアの SHA-256 が既にある場合は以前の結果を返し、再推論・二重登録しません。
3. Positive と Negative を同じ設定・指示文なしの Qwen モデルで個別に encode。
4. L2 正規化し、**すべての既存 accepted** と正確な cosine を計算。
5. 両方の cosine が **0.95 以上（>=）**になる同じ accepted があれば duplicate。
   Negative が両方空ならそのスコアは1、片方だけ空なら0。
6. 同じバッチ内で先に accepted になったペアも比較対象。
   duplicate/review は accepted JSON には含めず、SQLite の別ステータスで保持します。

近傍は `min(positive_score, negative_score)` が最大の accepted。
同点なら先の ID を使用します。UI には両方のスコアと近傍 ID を表示します。
**0.95 は確率・正解率ではありません。** 意味的な誤判定はあり得ます。
このアプリはプロンプト再利用の判断を補助します。画像の見た目の重複は測定しません。
同じプロンプトで異なる seed の画像は同一ペアとして扱います。

モデル ID、revision、ファイルハッシュ、dimension、正規化/encode方式、max tokens、dtype、
threshold と判定規則を DB に固定します。異なる設定を混ぜる処理は拒否します。
固定するのは最初の有効なベクトルを保存するトランザクション内だけです。
メタデータ不明やトークン超過のreviewだけでは固定せず、部分的なベクトルも保存しません。
モデルや threshold を変更する場合は新しいルートで再取り込みしてください。
入力は勝手にトリム/正規化しません。特殊トークン込み **32768 tokens** を超えたら要確認です。
本文の上限は各128 KiB。長文はCPU時間とRAMを多く使います。黙って truncate しません。
ベクトルのゼロ値・NaN・Infinity・dimension不一致は拒否します。

## 対応メタデータ

| 入力 | 対応範囲 |
| --- | --- |
| PNG | `parameters` の A1111 / Forge / Neo-Forge infotext (`Steps:` 行必須)、`prompt` の対応 ComfyUI API graph。tEXt / zTXt / iTXt |
| JPEG | EXIF UserComment の ASCII / Unicode infotext。圧縮画像データより前で読み取り停止 |
| ComfyUI | 単一 KSampler / KSamplerAdvanced の positive/negative が直接 CLIPTextEncode の text に接続されているもの |
| JSON | `{"positive":"a yellow flower","negative":"blur"}` または positive_prompt/negative_prompt、対応 ComfyUI graph |

multi-stage graph、conditioning変換、dynamic/custom node、競合メタデータ、欠落した本文は
**review**。グラフや文字列をコードとして実行しません。メタデータがなければプロンプトは復元できません。
FaceDetailer等の追加positive/negative/conditioning入力や、別ノードがCLIPTextEncode出力を
消費するグラフも要確認とし、最初のsamplerだけを黙って部分採用しません。
A1111 が negative 行を省略した場合だけ、そのフォーマットの規約として空を認識します。
JSON で negative が欠落している場合は推測しません。
JSONに複数のprompt表現がある場合はすべて検証し、同じペアで一致した場合だけ採用します。
競合・一部欠落や非標準の `Negative prompt:` ラベルは要確認です。
要確認記録は UI の「確認・補正」から入力し直すと同じコアで再判定できます。元記録は残ります。
解決済みの同じreviewを再補正するとHTTP 409で拒否し、最初の補正リンクを保持します。
エクスポート JSON は集約データ形式です。単一画像のサイドカー JSON と異なり、そのまま再取り込みする形式ではありません。

## CLI / エージェント

```powershell
# 入力は利用者が input/ 以下へ配置した対象だけを指定
uv run --frozen --extra model prompt-ledger --root . import selected/example.png --check-only
uv run --frozen --extra model prompt-ledger --root . batch selected/a.png selected/b.json --dry-run
uv run --frozen --extra model prompt-ledger --root . import selected
uv run --frozen --extra model prompt-ledger --root . status
uv run --frozen --extra model prompt-ledger --root . records
uv run --frozen --extra model prompt-ledger --root . export --output accepted.json
```

フォルダ指定は直下だけ。下位も含める場合は `--recursive` を明示。
明示した引数順は保持します。フォルダ内の展開だけを相対パス順に整列し、
その引数の位置へ挿入します。結果配列もこの処理順に対応します。
1回につき1–20ファイル、1ファイル10 MiB、合計20 MiB、metadata合計1 MiB。
拡張子対象外やZIPは解析しません。symlink/junction、絶対参照、`..`、Windows ADSを拒否します。
通常のCLI stdoutは常に単一JSON、diagnosticsはstderr。`--help`のみ人間向けテキストです。
すべてのJSON応答に `schema_version: 1`。

| exit code | 意味 |
| --- | --- |
| 0 | 処理成功（duplicateや再利用も正常） |
| 1 | 実行環境/IOの失敗 |
| 2 | 入力/設定/モデル/パスのエラー |
| 3 | 処理は完了、要確認の項目あり |

`--check-only` は解析だけでモデル・DBを使いません。
`--dry-run` は同じ判定を実行しトランザクションを rollback。
初回は空のDB/スキーマだけ作成しますがレコード・判定設定は保存しません。
dry-run結果のIDは仮のもので、補正対象に使わないでください。
importはバッチ全体を1トランザクションにし、他のimportと直列化します（SQLite busy timeout 60秒）。
先行の埋め込み中は書き込みを待ちます。大規模ANN最適化は未実装です。
exportはスナップショットを読み、一時ファイルをfsyncして同じディレクトリ内でatomic replaceします。

## JSON API

`GET /openapi.json` に機械可読のAPI仕様があります。CDN依存の docs UI は搭載しません。

```python
import base64
import json
import urllib.request

base = "http://127.0.0.1:8765"
session = json.load(urllib.request.urlopen(base + "/session"))
body = {"files": [{"name": "synthetic.json", "content_base64": base64.b64encode(
    b'{"positive":"a yellow flower","negative":"blur"}'
).decode()}], "check_only": True}
request = urllib.request.Request(base + "/api/import", data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json", "X-Ledger-Token": session["token"]})
print(json.load(urllib.request.urlopen(request)))
```

GET `/api/status`, GET `/api/records?privacy=true&limit=100&offset=0`, POST `/api/import`,
POST `/api/review/{id}`, GET `/api/export`。
セッショントークンは起動ごとに変わり、`/api/*` 全てに必要です。
Host/Originは設定ポートの127.0.0.1のみ、CORS開放なし、外部サイトのfetchを拒否します。
`records` 初期値とimport結果は本文/ファイル名を返しません。
`privacy=false` またはCLI `--include-text` は明示的な本文表示です。
**exportは採用済み本文を含みます**。保存先は外部同期されない場所を選んでください。
UIは外部リソースなし、画像要素なし、CSPで画像ロードを禁止します。
APIに任意パスを読み取らせるエンドポイントはありません。

## 相対パスと移動

`config.json` 内はルート基準の相対パスのみ。既定は data / input / models / outputs。
CLI `--root`、次に環境変数 `LEDGER_ROOT`、指定がなければソースパッケージの親がルートです。
CWDはデータ参照の基準にしません。外部へインストールした場合は `--root` を明示してください。
`--root` の絶対パスは実行時だけ使い、DBやexportへ保存しません。
source参照はinputルート相対、ブラウザでは選んだファイル名だけを保存します。
`.venv` は移動せず、新環境で `uv sync --frozen --extra model` して再構築してください。

## 開発と検証

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check prompt_ledger tests
node --check prompt_ledger/static/app.js
node --test tests/test_ui_privacy.cjs
```

テストは合成の無害なJSON/PNG/EXIFと数学的mock vectorsのみ。
0.95境界、negative差、empty、同バッチ重複、再取り込み、model混在拒否、破損metadata、
曖昧graph、path traversal、junction、別CWD、日本語/スペースroot、同時transaction、atomic export、
origin/token/privacyを確認します。Qwen実モデルの意味精度・CPU性能は別途実測が必要です。
詳細と未検証事項は `docs/VALIDATION.md`。

## 再配布 / 公開

本体は [MIT](LICENSE)。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照。
画像、実prompt履歴、DB、export、重み、秘密情報、`.venv` はGit管理しません。
`.gitignore` は補助であり、公開前に `git ls-files` と staged diffを必ず確認してください。
安全なソース配布のチェックは `docs/RELEASE_CHECKLIST.md`。
共有ホスティング、0.0.0.0 bind、認証無効化、外部API追加はこのアプリの対象外です。
