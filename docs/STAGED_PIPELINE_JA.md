# 抽出JSON → embedding・cosine → 別JSON

画像の解析をやり直さずに比較を再実行できる段階処理です。JSONを挟むだけで速くなる保証はありません。
通常の画像直接importも残しています。画像の画素を表示・復号せず、元画像・元JSONを変更しません。

## UI

1. 画像・親フォルダを選び「テキストをJSONに抽出（比較なし）」でmetadata.jsonを保存。
2. 「抽出JSONから比較して別JSONを保存」でそのJSONを選択。
3. CPU / CUDA GPU / autoを選び「JSONを比較」。CUDAは比較開始時にモデルをGPUへロードします。
4. 完了後「比較済みJSONを保存」。元JSONと別のファイルを選んでください。

CUDAには既存のCUDA extra・モデル導入・対応ドライバが必要です。本機能はドライバやTLSを変更しません。
モデルはoffline/local_files_only/trust_remote_code=False、float32を維持します。
進捗にはrequested/selected/loadedとcosine backend/fallbackを表示します。上部の表示は通常import側の
モデル状態で、JSONジョブの実行先はジョブ進捗を確認してください。同じ要求modeではモデルを再利用します。
CPU/CUDA/autoは別インスタンスを保持するため、modeの選び直しで複数モデルが常駐する場合があります。
この場合の実VRAM/RSSは未測定です。外部プロセスのモデルをunloadする操作は行いません。

autoはCUDA利用不可と判断した場合にCPUを選び、その理由を表示します。
CUDAのembeddingがOOM等で失敗した場合は停止し、途中からCPUのvectorを混ぜません。
CPUでやり直すには、CPUと互換な別台帳で元JSONを再指定してください。既存台帳のvector-space、
revision/runtime/device/thresholdが一致しない場合も停止します。
CUDA cosineだけの失敗は同じvectorのCPU比較へ戻れます。embeddingの切替とは区別して表示します。

## CLI / エージェント

```powershell
.venv\Scripts\python -m prompt_ledger.cli --root . extract selected --recursive --output metadata.json
.venv\Scripts\python -m prompt_ledger.cli --root . --device cuda compare-json metadata.json --output compared.json --progress
```

compare-jsonの入力は既定でoutput_dir内（extractの出力先）です。input_dir内のJSONは--from-input。
--batch-sizeは1〜16レコード、既定8。--progressは本文を含まないJSONをstderrへ出し、stdoutは最終結果JSON。
入力はこのアプリが出力したschema_version=1, kind=metadata-extractionのrecords配列です。
schema_version/kindがrecordsより先に必要です。採用JSON・比較結果JSONや任意の配列を再importする形式ではありません。
入力・出力は別pathが必要です。CLIは同一pathを拒否します。

## 判定と出力

現在の保存済み除外ルールをembedding前と出力時に適用します。sourceで既にexcluded/reviewの行は再比較しません。
これらの本文なし行は元JSONに保持し今回countsへ集計しますが、DBへの再登録は行いません。
除外時に本文が省かれた元JSONを使っても本文は復元できません。ルールを緩めて再採用する場合は元画像から再抽出します。
同バッチの先行acceptedと既存DBに対し、従来と同じpositive AND negative >= threshold（既定0.95）を使います。
両negative空=1、片方空=0、最小id tie、math.fsumによる最終判定を維持します。

compared.jsonにはschema_version/space/recordsに加えてcomparisonとdecisionsが入ります。
recordsは台帳全体の現在の採用ペア（既存分を含む）。duplicate/excluded/reviewは含みません。
decisionsは台帳の各id/status/reason/nearest_id/positive_score/negative_score等で、本文・source・vectorは含みません。
comparisonは今回JSONの件数・再利用件数・実行先・時間・input_sha256等です。
今回の入力件数と台帳全体のrecords/decisions件数は一致しない場合があります。
通常のaccepted exportの形式は変更していません。

## バッチ化と互換性

JSONは全件展開せず、1値のmetadata予算を守って読み、最大16レコードの範囲だけを保持します。
既存のexact pairと除外一致は先に除き、残りのpositive/negativeをまとめてencodeします。
CPUはbatch1。CUDAは最大8テキスト、最大token長×batch数4096以内へ縮小し、長文はbatch1。
各テキストの32768 token制限は事前に検査し、黙って切り詰めません。
一つの長文でbatch encodeが拒否された場合は個別判定に戻し、その行だけreviewにします。
比較の128候補blockとexact cosineは維持します。全vectorや全JSONをRAMに永続cacheしません。

CUDAのbatching identityはtexts-max8-padded4096-v2へ更新されています。
旧CUDA台帳は黙って再利用せずmodel_mismatchになります。新しい台帳で元JSONから作り直してください。
CPU identityは従来どおりです。CPU/CUDAを切り替えて既存DBのvectorを混ぜる移行は行いません。

## 中止・再実行

各バッチのvector・判定を同じBEGIN IMMEDIATE transactionで確定します。現在のバッチは中止時にrollbackし、
以前に完了したバッチは保持します。全JSONを一括rollbackするモードではありません。
GPU kernel/モデルロードは強制停止せず、その呼出しの終了後に中止を確認します。他のプロセスを停止しません。
upload中のAPI cancelは、そのrequest専用のcancel scopeへ通知します。次のchunkや切断が来なくても
受信待ちを終了し、source.partのcleanup後にactive lockを解放します。実行中のdisk書込みは安全に終了を待ちます。
DB接続を再利用しますがcommitはバッチごとです。除外ルールが途中変更された場合は次のバッチ前に停止。

進捗pollの通信/HTTP失敗ではjob IDを保持し、「監視を再接続」と中止を残します。
terminal状態を確認するまでは新しいjobでIDを上書きしません。状態GETとcancel要求には10秒のAbort timeoutを設定。
通信失敗はworkerの停止を意味しません。全接続が失われた場合は、復旧後に同じjobを監視/中止します。

再開は元JSONを再指定します。先頭からJSONを読む必要はありますが、DBの保存済みpairを再利用し、
同じ空間・同じ入力なら既処理ペアを再embeddingしません。durableな台帳を使ったreplayで、
JSONのoffset checkpointや未確定GPU出力を信頼する方式ではありません。
不正なJSONの末尾を後で検出した場合も、それ以前の確定バッチは残ります。

出力はtemporaryへ逐次書込み、flush/fsync後にatomic置換。失敗/中止で旧出力を保持します。
最終publishingに入った後の取り消しは受け付けません。強制killではtemporaryが残る場合があります。
出力は単一read snapshotで現在のルールを適用し、export_rule_versionも記録します。

HTTP uploadは元JSONのlocal copyをoutput_dir/staged/<job_id>/source.jsonへ保持し、結果は同じ場所のcompared.json。
ジョブ状態のメモリ履歴は32件で、server再起動で状態照会は失われますがsource/resultとDBは残ります。
元JSONを再指定して別jobで再開できます。処理用snapshotも一時的に作るため、JSONとDB・結果分のdisk容量が必要です。
ブラウザの元ファイルは変更しません。直接保存では元JSONと同名の保存先を拒否します（別フォルダでも同名は不可）。
元ファイル名がcompared.jsonなら既定の保存名もcompared-result.jsonへ変えます。

## JSON API

- POST /api/compare-json?job_id=<32文字hex>&device=cpu|cuda|auto&name=metadata.json — raw JSON upload。
- GET /api/compare-json/<job_id> — 本文のない進捗。
- POST /api/compare-json/<job_id>/cancel — 中止要求。既完了/最終確定中は受付false。
- GET /api/compare-json/<job_id>/result — 完全な結果だけdownload。

全て既存のloopback Host/Origin/token境界を使います。同時JSON upload/比較は1件。
既存の画像直接importも同じSQLiteでserializeされます。相手のプロセス・モデルは停止しません。

## 測定と未検証

合成PNG metadataと32次元の数学vectorだけを使った1回測定では、100件の直接import+export約0.145秒に対し
抽出+段階比較約0.169秒、500件は約1.484秒に対し約2.074秒でした。
段階処理の再実行は約0.017秒/0.053秒、直接処理の再実行+exportは約0.011秒/0.032秒。
両方式とも再実行の追加embeddingは0回です。既存DB再利用はJSON段階化だけの効果ではありません。採用ペアは完全一致。
段階処理は追加のJSON I/O、batch commit、判定auditを持ち、同一の出力仕事量ではありません。
この測定はQwenやCUDAの速度を表しません。初回が速いという結論には使えません。
実Qwen/CUDAの時間・VRAM・batchによる数値差や意味精度は未測定です。開発ではGPUを使用していません。
CPU/GPUの実測は他作業と競合しない別の機会に行う必要があります。

API/CLIは逐次ですが、File System Access非対応browserのdownloadはBlobを全件保持します。
Python heap検査はSQLite/Torch native allocator、RSS、OS cache、VRAMの上限保証ではありません。
