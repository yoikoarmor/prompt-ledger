# 比較ありJSONを作る通常経路

通常は「比較して取り込む」→「採用JSONを保存」です。入力が画像metadataでも対応JSONペアでも
同じcoreで処理します。既存acceptedと同バッチの先行acceptedへ照合し、positive AND negativeが
設定閾値（初期0.95、以上を含む）ならduplicateです。両negative空は1、片方空は0です。
acceptedのみを出力し、duplicate/review/現在の除外ルール一致はJSONから外します。
既存acceptedも現行ルールで再フィルタして出します。元の記録や画像を削除しません。
同じ本文の再取込は既存行を再利用するため、JSONに二重追加しません。
試行・check-only・比較なしextractはacceptedを追加しない別モードです。

CLIは `import selected-folder --recursive` の後 `export --output accepted.json`。
APIは認証済み `POST /api/file?name=relative.png`（raw body、extract_onlyを付けない）の後
`GET /api/export`。全ファイルを同一バッチで処理する既存JSON APIも同じcoreを使います。

# 本文抽出と重複比較

## 本文をJSONへまとめる

UIの「テキストをJSONに抽出（比較なし）」は選択ファイルを順に処理します。
明示的な出力操作なのでJSONにはpositive/negative本文と相対sourceが入ります。
通常の本文非表示UIへ本文を表示せず、embedding・モデル読込・semantic dedupを行いません。
同じ本文も各sourceのレコードとして残します。保存済み除外ルールに一致した本文は出力せず、
excludedという理由だけを残します。曖昧なgraphや本文欠落はreviewです。seed等の追加生成設定は
今回の出力対象ではなく、確定したpositive/negativeを保持します。
JSONはkind=metadata-extraction、recordsのstatusはextracted/excluded/reviewです。
これは採用済みデータだけを出す従来のaccepted.jsonとは別の形式です。

CLI例（input_dirとoutput_dirはプロジェクト相対の設定）:

```powershell
.venv\Scripts\python -m prompt_ledger.cli --root . extract selected-folder --recursive --output metadata.json
```

CLIは単一のread snapshotの除外ルールを使い、逐次出力とatomic置換を行います。
SQLiteのレコードやvectorは作成しません。初回には空のledger/rules storeを作成する場合があります。
UIはshowSaveFilePicker対応環境で逐次保存し、それ以外はBlobによる全件保持です。
UIの中止では保存をabortします。UI抽出中に除外ルールversionが変わると中断して再実行を求めます。
APIは認証済み同一loopbackへの `POST /api/file?name=folder/file.png&extract_only=true`。
bodyはraw bytes、responseはschema_version/record/rule_version。これは明示的に本文を返すendpointです。
check_only/batch_idとの併用は拒否します。

## CUDAと比較時間

通常の起動はCPUです。CUDA版依存の導入だけでは切り替わりません。
`setup.bat --cuda`の後、`start.bat --device cuda`を明示します。
UIのrequested/selected/loaded表示で設定・選択・ロード状態を確認してください。
embeddingのモデル推論と、既存SQLiteのベクトルを読むcosine比較は別の処理です。
比較は既存acceptedを128件ずつ読み、JSONを復元して検証します。CPU時はNumPyの分割積演算、
CUDA時は同じブロックをfloat64でGPU計算します。NumPyなしではPythonで比較します。全体速度は取込件数・accepted件数・
重複率・同じnegativeの繰返し・入力読取・モデル読込にも依存します。

## 一次元化の解釈

1024要素の一次元配列は通常のembeddingそのものです。NumPy行列演算は次元を維持して
同じcosineをまとめて計算できます。準備・DB/JSON読込を含む全体時間とは別に評価します。
丸め誤差で閾値境界が変わり得るため、本体採用時は境界の厳密再計算とbounded block等が必要です。
本体は128件ずつの分割積演算と、近接候補の元cosineによる厳密再計算を使います。

単一スカラーに潰したcosineは、非ゼロなら+1か-1、ゼロは未定義です。
細かい意味類似度を表せず、1024次元の0.95をスカラーに流用できません。
正規化ベクトルと単位方向uではcosine>=tなら射影差<=sqrt(2-2t)です。
この幅に数値誤差の余裕を加えた候補絞込と元cosineの再確認なら候補漏れを抑えられますが、
候補が全件のままになる場合があり、最悪計算量は変わりません。
狭い窓やtop-kには同じ保証がありません。positive AND negativeの基準も維持が必要です。

閾値ごとの一致数は意味的正確さではありません。正解ラベルがないデータではprecision/recallや
最適閾値は確定できません。人がローカルで別途ラベルを付けた評価セットが必要です。


## 分割比較・GPU batching

比較の追加メモリは件数全体ではなくブロック幅128×embedding次元に依存します。
全N×N行列や全vectorの永続RAM cacheは作りません。1回の照合中だけ最大64個のserialized vector復元結果を保持し、同じnegative等の再JSON復元を省きます。照合終了時に破棄し、キーは実際のvector文字列なのでDB更新を古い値で隠しません。DB読込と新規JSON復元は依然必要です。
candidate blockを検証し、行列スコアの最大値・現在の最大値から1e-10以内の候補だけを
従来のmath.fsum cosineで再確認します。確定値で閾値判定し、同点は最小idです。
ゼロ・NaN・非正規化・異なる次元等の保存vectorはstored_embeddingで停止し、新規取込をrollbackします。
Python/NumPy/GPUともpositive AND negative、両negative空=1、片方空=0を維持します。

CUDA比較はfloat64でTF32を使いません。実行失敗やVRAM不足は同じvectorのCPU比較へ戻り、
status.comparisonとUIにbackend/fallbackを表示します。embeddingのdeviceや空間は変更しません。
embedding自体のCUDA失敗は従来どおり明示エラーであり、CPUへ黙って変更しません。
GPUへの転送・同期・float64演算もあるため、小さなデータではCPUより速い保証はありません。
DB/JSONの負荷はGPU化では消えません。

CUDAのembeddingはpositive/negativeを最大2件でencodeします。padding後tokens×件数が
4096を超える場合はbatch1です。CPUはbatch1。各入力を事前に数えて32768超は切り詰めず拒否します。
CUDAのvector-space identityにbatching方式を記録したため、従来CUDAで作ったledgerとの混在は拒否します。
変更後は新しいledger rootで元データを再取込してください。元DBや画像を削除する必要はありません。

新GPU経路の回帰はスタブのみです。今回GPUが他作業で使用中のため、GPU比較の実時間・VRAM・
CPU/GPU embeddingの意味精度や全体一致率は未測定です。以前の単文CUDA smokeとは別の検証です。


## 計測の読み方と制約

比較の追加RAMは、SQLiteのnative cache・Torch allocator・OS cacheを含まないPython heapとは
別です。tracemallocの値だけでプロセスRSSやVRAM全体を保証しません。大規模取込は総照合数が
二次的に増え得ます。近接候補や同点が多いと厳密再計算も増えます。全て異なるJSON vectorでは
復元cacheの効果が小さく、GPU転送が遅い機器ではCUDA比較がCPUより遅い場合もあります。
今回の実データ計測は270件由来のvectorを固定した候補DBへの照合であり、UIの全取込時間や
意味的重複の正解率ではありません。追加の大規模計測はベクトルを反復した合成DBです。


## 抽出キャンセル

抽出中は各fetch・各JSON書込みの前後と保存確定直前に固定AbortSignalを確認します。
最後のrecordやJSON終端書込み中の中止も保存先abortへ進み、成功表示を出しません。
OSの保存確定(close)を開始した後の取消しは保証できないため、その短い確定段階では中止ボタンを無効にします。
