# フォルダ・大量入力・CUDA

## 入力とメモリ

ブラウザの「フォルダを選択」は選んだフォルダの下位フォルダも対象にします。
PNG/JPEG/JSONだけを選別し、選択順に1ファイルずつ送信・処理します。
sourceは選択フォルダ名からの相対パスです。フォルダのドラッグ操作ではなく選択ボタンを使ってください。
CLIは従来どおりinputルート内の明示的なパスだけを扱います。

```powershell
.venv\Scripts\python -m prompt_ledger.cli --root . import selected-folder --recursive --check-only
.venv\Scripts\python -m prompt_ledger.cli --root . import selected-folder --recursive --dry-run
.venv\Scripts\python -m prompt_ledger.cli --root . import selected-folder --recursive
```

20件、1ファイル10MiB、合計20MiBという入力上限は撤廃しました。
CLIは64KiBずつ読み、ブラウザはFileをそのまま送信します。サーバーはPNGの非テキストチャンクと
JPEGの画像データを保持せず捨て、画像の一時ファイルやコピーは作りません。
画素をデコードしません。入力全体のハッシュは逐次計算します。
選択リストは先頭100件、処理結果の明細は先頭200件まで表示/返却し、全件数はprocessed/countsで返します。
results_truncatedは明細だけが省略されたことを示し、処理件数の制限ではありません。

**メタデータ安全予算（1MiB）、本文予算、モデル32768トークン上限は維持**します。
JSONは全体がメタデータなので予算超過は要確認です。切り詰めて採用はしません。
圧縮爆弾・壊れたデータ・読取権限エラーは個別のreview理由で報告します。
unsupported拡張子はフォルダ列挙から除外し、symlink/junctionは追跡せず要確認にします。
循環リンクを辿りません。日本語・空白・長い相対パスを扱いますが、OSの長いパス対応に依存します。
1フォルダ内の名前のソートやブラウザのFileListは件数分のメモリを使います。
類似比較は正確な全件cosineなので、件数増加に伴い計算時間も増えます。

## API・キャンセル・トランザクション

大きなファイルは `POST /api/file?name=folder/file.png` へapplication/octet-streamで
そのまま送信します。従来どおりX-Ledger-Token、正確なloopback Host/Originが必要です。
`check_only=true` は解析だけです。APIにローカルパスを読ませる機能は追加していません。
互換用 `/api/import` はJSON/base64を一括解析するため**リクエスト全体の安全予算**を残します。
大容量には `/api/file` を使ってください。こちらにファイルサイズの固定上限はありません。

通常のUI取込は1ファイルごとにcommitし、後続は先行採用済みを参照します。
中止ボタンは送信・次のファイル処理を中止します。処理済みは残り、同じ入力の再実行は冪等です。
中止と同時にサーバー処理が完了した1件は保存されている可能性があるので一覧を更新してください。
GPU/CPU実行中の1回の推論を強制終了しません。

UI試行は `POST /api/dry-batch` で開始し、返ったbatch_idを各 `/api/file` に付けます。
先行の仮採用を含めて比較し、`DELETE /api/dry-batch/{batch_id}` で全件rollbackします。
同時試行は1つ、120秒無通信なら破棄します。試行中は他の書き込みが待つ場合があります。
CLIはバッチ全体で1トランザクションを保ち、Ctrl+Cでrollback・終了コード130です。
モデルエラーなど継続できない場合は明示エラーで停止します。

## CUDA

既定はCPUのままです。ユーザーが使う場合だけ以下を実行します。

```powershell
.\setup.bat --cuda
.\start.bat --device cuda
# CUDA利用不能ならCPUを選ぶモード
.\start.bat --device auto
```

CUDA 13.0版PyTorchを公式indexからuv.lockに従って導入します。追加通信量・容量は大きくなります。
CPU版とCUDA版extraは排他的です。ドライバやシステム設定は変更しません。
対応するNVIDIA GPU/ドライバが必要です。公式手順: https://pytorch.org/get-started/locally/
Linuxでは `uv sync --frozen --extra cuda` を使えます。
CLI共通の `--device cpu|cuda|auto`、またはconfig.jsonの `device` で選択します。
CLI指定は保存しません。UIとstatusのexecutionに要求/選択デバイスとロード状態を表示します。

cuda指定で利用不能ならcuda_unavailable。autoは利用不能の場合だけCPUを選びます。
VRAM不足はcuda_oom、他のCUDA実行障害はcuda_runtimeで停止し、途中でCPUへ黙って切り替えません。
既存の別モデルをアンロードしたり、他プロセスのGPUを制御したりしません。
float32・batch1・オフラインを維持します。デバイス/実行環境が変わると既存vector空間との混在を
拒否するため、新しいledger rootで再取り込みしてください。
今回のCUDA検証はstubのみで、実GPUの性能・VRAM量・ドライバ互換性は未検証です。


## JSON出力

CLIとAPIは1件ずつJSONを書き出します。schema_version・space・recordsの形式とid順は互換です。
SQLiteの単一read snapshotで除外ルールを固定し、CLIは同じディレクトリの一時ファイルへ
書き込み、flush/fsync後にatomic置換します。中断・失敗時は旧出力を保持し一時ファイルを削除します。
APIの中断では読み取り接続を解放します。転送途中のJSONは不完全なので破棄してください。
ブラウザはshowSaveFilePicker対応環境では直接ファイルへ逐次保存します。非対応環境の
互換保存はBlobとして全結果をメモリに保持するため、大規模出力にはCLIを使用してください。
Pythonのexport()互換ヘルパーも全件展開します。通常CLI/APIはexport_chunks()を使用します。
長いread snapshot中はSQLiteの書込み待ちやWAL増大が起きる場合があります。


## 新規導入とCPU版からの切替

新規利用者は展開先で `setup.bat --cuda` を実行します。通常の `setup.bat` はCPU版です。
CPU版が入っている場合も、アプリを閉じてから同じ `setup.bat --cuda` を実行してください。
既存の専用.venvを使い、uv.lockのCUDA extraに同期してtorchをCPU版からCUDA版に置き換えます。
`--inexact`は無関係な追加パッケージを保持する指定であり、選択したtorchの置換を抑止しません。
手作業のuninstallやvenv/DB/モデルの削除は不要です。逆に通常setup.batの再実行はCPU版へ戻します。
通信・空き容量を要し、対応するNVIDIAドライバが必要です。ドライバやTLS設定は変更しません。
起動は `start.bat --device cuda`。依存導入だけでは実GPU推論の成功は保証しません。
既存vectorとdevice/runtimeが変わるため、新しいledger rootで利用してください。
公式wheel一覧: https://download.pytorch.org/whl/cu130/torch/
uv sync仕様: https://docs.astral.sh/uv/concepts/projects/sync/

今回の確認では公式indexに固定torch 2.14.1+cu130のWindows cp310〜cp313 wheelが存在しました。
実環境CPU版からのオフラインdry-runでCPU版の除去とCUDA版の導入計画を確認し、空の環境向け
計画も確認しました。dry-runのみであり、CUDAパッケージの取得・導入やGPU実行は行っていません。
