# 検証記録

## 0.2 の検証（2026-11-10）

Linux / CPython 3.11.16 / Node.js 24.21.0。Python 22件、Node 1件成功。4モード、承認、プロファイル・外部設定、不正設定、batch の権限制限・原子性、履歴・取り消し・古いID拒否・外部変更検出、v1 の読み取りと v2 移行、インストール更新時の設定保持を検証した。off の Codex 出力が空で、OpenCode のコンテキスト・トーストが増えないことも確認した。

NixOS の移植先 Python 解決のため、Node テストでは `UV_PYTHON` に現在の動作する Python を、`UV_CACHE_DIR` に書き込み可能な一時キャッシュを指定した。Python 本体や依存を配布物に固定していない。

skill-creator の quick_validate.py も既存キャッシュの PyYAML を使って成功。本体への依存追加はない。

今回、実ホストでのモデル実行・自然な修正依頼からの抽出精度・UI 表示は再検証していない。以下は 0.1 当時の検証履歴であり、0.2 の保証と区別する。タグ確認必須の旧仕様は 0.2 で変更し、タグは自動分類、ask は書き込み全体への承認になった。

## 0.1 の記録

実施日: 2026-11-08。環境: Linux / uv 0.12.17 / CPython 3.11.16 / Node.js 24.21.0。

## 完了条件との対応

| 仕様書の条件 | 結果 | 根拠 |
| --- | --- | --- |
| 1. 登録・一覧・取得・更新・無効化・削除 | 確認済み | lifecycle テスト。元発言の保持、UTC 日時、再有効化も確認 |
| 2. タグ OR・タグなし・有効のみ | 確認済み | 複数タグ、無関係タグ、無効記録、空検索、bootstrap の実 DB テスト |
| 3. タグ確認と未登録検出 | 実装・機械的検証済み | confirmed、existing/create の区別、空白除去・空・重複拒否。ユーザー確認を実際に行ったかはモデルの手順に依存 |
| 4. 一括更新の rollback | 確認済み | 更新・タグ新設・削除の後に存在しない ID を指定し全変更が戻る。SQL trigger による途中失敗も確認 |
| 5. 構造化エラー・ログ・stderr | 確認済み | 壊れた DB、ロック待機、権限エラーを模したログ障害、ローテーション、秘密情報の非記録 |
| 6. uv・標準ライブラリ・Git 除外 | 確認済み | Python 3.11 で14テスト成功。移植先を実際に git init し、DB/WAL/SHM/journal/log の check-ignore を確認 |
| 7. ホストの発言時実行・結果受け渡し・二段階取得 | **一部のみ確認、完了扱いにしない** | 以下のホスト別結果を参照 |
| 8. 固定絶対パス非依存・移植 | 確認済み | 空白を含む別プロジェクトにインストールし、さらに移動後、サブディレクトリから実際の Codex 設定コマンドを uv で起動。既存フック保持・更新時DB保持も確認 |

## 自動テスト

```sh
uv run --project . python -m unittest discover -s tests -v
node --test tests/opencode.test.mjs
```

- Python: **14 / 14 成功**。外部 Python パッケージなし。最低対応バージョンの Python 3.11.16 を明示して実行した。
- Node: **1 / 1 成功**。実 Python を起動し、synthetic part、トースト呼び出し、失敗時の例外を確認。これは OpenCode ホストそのものの検証とは区別する。
- skill-creator の `quick_validate.py`: **成功**。検証ツール専用の一時 uv 環境に PyYAML を追加した。本体、uv.lock、標準ライブラリテストの依存関係には追加していない。
- この配布元ディレクトリの `.git` は実 Git リポジトリではないため、配布元の `git diff --check` は使用できない。Git 除外は独立した一時 Git リポジトリで実証した。

## OpenCode 1.18.33

バージョン固定の公式資料を取得して確認した。

- [v1.18.33 plugins.mdx](https://github.com/anomalyco/opencode/blob/v1.18.33/packages/web/src/content/docs/plugins.mdx): ローカル `.opencode/plugins/` の読み込み、プラグイン関数の入力と hooks の返却。
- [v1.18.33 Plugin / Hooks 型](https://github.com/anomalyco/opencode/blob/v1.18.33/packages/plugin/src/index.ts): `chat.message(input, output)`、`output.message` と変更可能な `output.parts`。
- [v1.18.33 session/prompt.ts](https://github.com/anomalyco/opencode/blob/v1.18.33/packages/opencode/src/session/prompt.ts): ユーザーメッセージの保存前に `chat.message` を呼び出す箇所。

現在の公開サイトは新 API の資料になっていたため、今回の実装には v1.18.33 の資料を使用した。新しい OpenCode の `Plugin.define` API への互換性は主張しない。

実行:

```sh
uv run python tests/host_smoke.py opencode
```

実際の 1.18.33 サーバーを一時プロジェクトの localhost で起動し、2回のユーザー発言を `noReply: true` で送った。両方の応答に有効なタグなしの指針とタグ一覧があり、タグ付き指針はまだ含まれないことを確認した。次に CLI の `search` を実行し、タグ付き指針を追加取得した。DB パスを利用不能にした後の発言は HTTP エラーとなり、成功と誤認されないことも確認した。

実測結果:

```json
{"host":"opencode","version":"1.18.33","two_user_message_hooks":"passed","persisted_context":"passed","second_stage_cli":"passed","retrieval_failure":"passed","model_selected_tags":"NOT_TESTED","visual_toast":"NOT_TESTED"}
```

**未検証:** モデルに送る最終リクエストへの到達、モデル自身によるタグ選択・ツール呼び出し・指針適用、TUI のトースト視認。`noReply` はモデルを実行しない。これらの確認には、対象版で動くモデルプロバイダー、ツール実行権限、対話 UI が必要。

## Codex CLI 0.159.3

[公式 Hooks ドキュメント](https://learn.chatgpt.com/docs/hooks) の stdin JSON、`UserPromptSubmit`、`hookSpecificOutput.additionalContext`、`systemMessage`、exit 2、cwd、信頼手順を確認した。このページには版固定 URL がないため、ドキュメント全体が 0.159.3 と一致するとは断定しない。

対象バイナリから次のコマンドで生成した API スキーマも確認した。

```sh
codex --version
codex features list
codex app-server generate-json-schema --out /tmp/feedback-manager-codex-schema
```

0.159.3 では `hooks` が stable / true。生成した `HooksListResponse.json` は `userPromptSubmit`、command handler、statusMessage、timeout、trustStatus を含む。公式ドキュメントに従った出力 JSON は Python テストで確認したが、実モデルに追加されることは未検証。

実行:

```sh
uv run python tests/host_smoke.py codex
```

一時プロジェクトはホストから未信頼と判定され、プロジェクトの `.codex/hooks.json` は読み込まれなかった。CLI の一時的な project trust 指定でも読み込まれなかったため、ユーザーの永続設定を変更せず中止した。続いて同じコマンド定義を一時的な inline hook として渡し、実 app-server の `hooks/list` がイベント・設定を認識し、`untrusted` と返すことを確認した。テストスクリプトはこの metadata 確認を再現する。

```json
{"host":"codex","version":"codex-cli 0.159.3","inline_hook_schema":"passed","hook_trust_status":["untrusted"],"project_hook_discovery":"NOT_TESTED: project untrusted","trusted_execution":"NOT_TESTED: requires explicit hook trust","model_selected_tags":"NOT_TESTED"}
```

**未検証:** 信頼済みプロジェクトからのフック自動読み込み・発言時起動、additionalContext のモデルへの受け渡し、UI 通知、モデル自身による二段階取得と適用。前提は、利用者によるプロジェクトの信頼、`/hooks` での定義のレビュー・信頼、非管理フックを許可する管理ポリシー、利用可能なモデルとツール実行環境。信頼の迂回オプションは使用していない。

## 利用環境での残りの受け入れ確認

1. 一時的な確認用プロジェクトに導入し、タグなしの目印と「文書」タグ付きの別の目印を登録する。ask では保存内容全体を確認する。
2. OpenCode / Codex の各 UI で文書作成を依頼し、最初の回答より前の通知、フック由来のタグ一覧、モデルによる `search` のツール実行、取得した ID を確認する。
3. 別ジャンルの発言を送り、該当しないタグ付き指針が適用されないことを確認する。無効化後の発言で対象が候補から外れることも確認する。
4. 適用候補の矛盾を用意し、ID を示した確認と、今回限りの例外で DB を書き換えない動作を確認する。
5. Codex のフックを未信頼状態にした場合のスキップと、信頼後の実行を区別する。ホスト更新時にはこの手順を再実施する。

この確認は意味上の正しさを網羅的に保証しない。タグ選択・継続性判断・矛盾検出・指針適用はモデル依存という仕様上の限界が残る。

0.2 の追加受け入れ確認: 起動側でプロファイルを変え、通常の修正依頼から auto が明確な継続的希望を保存すること、ask が内容を確認すること、read_only/off が保存しないことを確認する。既存指針の矛盾と一度限りの修正で自動上書きしないことも確認する。
