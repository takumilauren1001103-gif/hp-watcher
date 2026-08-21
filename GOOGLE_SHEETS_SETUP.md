# Googleスプレッドシート保存への移行手順

この手順は、HP電話番号監視の保存先を`config.json`・`history.json`からGoogleスプレッドシートへ移すためのものです。移行後もTelegram通知、管理画面のURL追加・削除・通知済み復帰、全ページ探索、40日未検出の自動整理は維持します。

> **重要:** サービスアカウントのJSON鍵は、公開リポジトリ、GitHub Pages、管理画面、チャットへ貼り付けてはいけません。GitHub ActionsのRepository secretにだけ保存してください。GitHubは、secretを環境変数としてワークフローへ渡す方法を案内しています。[3]

## 完成後の構成

| 要素 | 用途 | 秘密情報の扱い |
|---|---|---|
| Googleスプレッドシート | URL、巡回状態、通知済み、48時間履歴の保存先 | シートは非公開。サービスアカウントと管理者Googleアカウントだけを編集者にする |
| サービスアカウント | GitHub Actionsからシートを読む・書く | JSON鍵を`GOOGLE_SERVICE_ACCOUNT_JSON`に保存。管理画面には置かない |
| Google OAuth クライアントID | 管理画面を開いた本人がシートを追加・削除・復帰する | クライアントIDは公開情報であり、管理画面のブラウザ保存にのみ保持する |
| GitHub Actions | 30分ごとに4ワーカーへURLを固定分割して巡回する | `GOOGLE_SHEETS_ID`はVariable、JSON鍵はSecret |

Googleの公式ガイドでは、特定のGoogle Sheetへのサービスアカウントアクセスは、ファイルをサービスアカウントのメールアドレスへ直接共有する方式で実現でき、管理者ロールやドメイン全体の委任は不要と説明されています。[1]

## 1. Google CloudプロジェクトとAPIを準備する

Google Cloud Consoleで新しいプロジェクトを作成するか、既存の個人用プロジェクトを選びます。続いて **Google Sheets API** を有効にしてください。管理画面からのユーザー認証とGitHub Actionsからのサーバー認証を分けるため、次の2種類の認証情報を作成します。[1] [2]

| 認証情報 | 作成場所 | 用途 |
|---|---|---|
| サービスアカウント | **IAM と管理** → **サービスアカウント** | GitHub Actionsが無人で監視・保存するために使用 |
| OAuth 2.0 クライアントID（ウェブアプリケーション） | **Google Auth Platform** → **Clients** | 管理画面を使う本人のGoogleログインに使用 |

サービスアカウントには分かりやすく`hp-watcher`などの名前を付けます。プロジェクト内の追加ロールは不要です。作成後、**Keys** → **Add key** → **Create new key** → **JSON**を選び、JSON鍵をダウンロードします。この鍵は再ダウンロードできないため、安全な場所にだけ一時保管してください。[1]

## 2. 空のスプレッドシートを作成して共有する

新しい空のGoogleスプレッドシートを1つ作成します。共有設定を **制限付き** のままにし、サービスアカウントのメールアドレス（`...iam.gserviceaccount.com`）を**編集者**として追加します。サービスアカウントには受信トレイがないため、通知送信は不要です。[1]

スプレッドシートURLの次の部分を控えます。これは秘密情報ではありません。

```text
https://docs.google.com/spreadsheets/d/ここがGOOGLE_SHEETS_ID/edit
```

## 3. 管理画面用のOAuthクライアントを作成する

Google Auth PlatformでOAuth同意画面を設定します。個人だけが使う場合は、同意画面のテストユーザーに管理者本人のGoogleアカウントを追加してください。次に **Create Client** で **Web application** を選び、**Authorized JavaScript origins** に次の値を1行ずつ追加します。[1]

```text
https://takumilauren1001103-gif.github.io
http://localhost:8000
```

作成後に表示される`...apps.googleusercontent.com`形式の**クライアントIDだけ**を控えます。クライアントシークレットは管理画面では使いません。Googleはブラウザで動くJavaScriptアプリにWeb application型のOAuthクライアントを使うよう案内しており、この形ではクライアントシークレットを使いません。[1] [2]

## 4. GitHub Actionsへ秘密情報と設定値を登録する

リポジトリの **Settings** → **Secrets and variables** → **Actions** を開きます。`Secrets`タブには次の1件を追加し、`Variables`タブには次の2件を追加します。Repository secret / variableは、リポジトリの設定画面から作成できます。[3] [4]

| 種類 | 名前 | 入れる値 |
|---|---|---|
| Secret | `GOOGLE_SERVICE_ACCOUNT_JSON` | ダウンロードしたサービスアカウントJSONの**全文** |
| Variable | `GOOGLE_SHEETS_ID` | 手順2で控えたスプレッドシートID |
| Variable | `STORAGE_BACKEND` | この時点ではまだ作らない。移行確認が完了してから`sheets`を設定する |

`STORAGE_BACKEND`を未設定のままにすると、システムは従来のJSON保存・単一ワーカー監視を続けます。このため、コード公開後も、上記の値を設定するまで現在の監視は止まりません。

## 5. 初期化と既存データ移行を行う

サービスアカウントJSONとシートIDをGitHub Secret / Variableへ入れた後、初回だけ`initialize_sheets.py`を実行します。このスクリプトは次の5タブを作成し、既存の設定と履歴をコピーします。

| タブ名 | 保存内容 |
|---|---|
| `Active_URLs` | 監視中URL、固定シャード、次回予定、内部ページ探索キュー、エラー回数 |
| `Notified_URLs` | Telegram通知済みURLとマスク済み番号 |
| `Recent_Runs` | 直近48時間の巡回結果。最大2,000件へ自動整理 |
| `Removed_URLs` | 手動削除・40日未検出による削除履歴 |
| `Settings` | 巡回・検出パラメータ |

初期化は**PC不要**でGitHub Actionsの手動実行から行えます。`Actions` → `HP電話番号監視` → `Run workflow`を開き、**実行内容**で`initialize-sheets`を選んで実行してください。この専用ジョブは、手順4で登録したRepository secret / variableを環境変数としてだけ利用し、JSON鍵をログやリポジトリへ保存しません。[3]

完了すると、ログに`移行完了: 監視中 ... 件 / 通知済み ... 件 / 履歴 ... 件`と表示され、スプレッドシートに5つのタブが作られます。**初期化完了が確認できるまで`STORAGE_BACKEND=sheets`を設定しないでください。** 初期化を繰り返しても、URL・通知済み・履歴が重複しないよう実装されています。

## 6. 影運転から本番切替へ進める

初期化済みのシートを確認したら、まず監視URLを50件までにして`STORAGE_BACKEND=sheets`をRepository Variableとして登録します。以降は30分ごとに4つのワーカーが同時に起動し、URLハッシュで決まる各シャードだけを処理します。1ワーカーは最大11 URLを処理するため、500 URLでは1 URLあたりおおむね4回/日を目標にできます。

| 段階 | 期間 | 合格基準 |
|---|---:|---|
| 50 URL | 72時間 | 15分超過0回、シート更新失敗0回、重複通知0件 |
| 150 URL | 72時間 | 各URLが1日3回以上、未保存状態0件 |
| 300 URL | 7日 | 95パーセンタイルのworker実行時間が12分未満 |
| 500 URL | 7日 | 各URLが1日3回以上、重複通知0件、状態回復100% |

GitHub Actionsの定期実行には外部要因による遅延があり得ます。そのため、4回/日を形式上の保証値とはせず、`Recent_Runs`で実行開始時刻とURLごとの巡回回数を確認しながら運用してください。遅延が起きた時は、実装済みの`next_due_at`により長く未巡回のURLから優先して回復します。

## 7. 管理画面の接続方法

管理画面を開き、保存先で **Googleスプレッドシート保存** を選びます。シートIDとOAuthクライアントIDを入力して接続すると、Googleログインとシート操作の許可が求められます。承認後、現在と同じ画面でURLの追加、一括追加、削除、通知済みURLの復帰、直近24時間履歴を扱えます。

管理画面はサービスアカウント鍵を保持しません。短時間のGoogleアクセストークンだけをブラウザのメモリに保持し、ページを開き直した時はGoogleログインをやり直します。OAuthアクセストークンには有効期限があるため、一定時間後に保存操作が失敗した場合は、更新または再接続を行ってください。[2]

## 参考資料

[1]: https://developers.google.com/workspace/guides/create-credentials
[2]: https://developers.google.com/identity/protocols/oauth2
[3]: https://docs.github.com/actions/security-guides/using-secrets-in-github-actions
[4]: https://docs.github.com/actions/learn-github-actions/variables
