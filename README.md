# Feedback Manager v1.1.1

プロジェクト内の継続的なフィードバックを保存するスキル。Python 3.11 以降、uv、標準ライブラリだけで動作する。SQLite は `sqlite3` を使用し、外部 Python パッケージは不要。

## 導入

このディレクトリを配布元として、既存の対象プロジェクトにインストールする。

```sh
uv run --project . scripts/install.py /path/to/project
```

`.agents/skills/feedback-manager/` にスキル、`.codex/hooks.json` にフック、`.opencode/plugins/feedback-manager.js` にプラグインを配置する。既存 Codex フックは保持して追記する。変更済みのインストールを更新するときは差分を確認して `--update` を付ける。DB はコピー・更新対象外。インストール自体は複数ファイルにまたがるため原子的ではなく、失敗した場合は原因を直して再実行する。

対象プロジェクトで一度 `uv run --project .agents/skills/feedback-manager python --version` を実行し、Python が利用可能であることを確認する。初回の Python ダウンロードにはネットワークが必要な場合がある。uv のキャッシュに書けない環境では `UV_CACHE_DIR` を書き込み可能な場所へ設定する。特定マシンの Python パスを配布物に固定しない。

### NixOS での Python 設定

uv が取得した汎用 Linux 向け Python は、NixOS の動的リンカーを見つけられずに起動できない場合がある。システム設定を変更せずに使うには、Nix が提供する Python を Feedback Manager 専用に指定する。以下は対象プロジェクトのルートから実行する。Nix の flake 機能と `uv` が必要で、初回の取得にはネットワークが必要な場合がある。

まず、配置済みの `.agents/skills/feedback-manager/` に Python へのリンクを作成する。Python 本体は Nix ストアに置かれる。

```sh
nix build nixpkgs#python312 \
  --out-link .agents/skills/feedback-manager/.nix-python
```

次に、そのパスをこのインストール専用の `.python-version` に記録する。

```sh
printf '%s\n' "$PWD/.agents/skills/feedback-manager/.nix-python/bin/python3" \
  > .agents/skills/feedback-manager/.python-version
```

インストーラーと、後から動くフックはいずれも Feedback Manager のプロジェクトを指定して実行するため、この Python を参照する。

```sh
uv run --project .agents/skills/feedback-manager \
  .agents/skills/feedback-manager/scripts/install.py .

uv run --project .agents/skills/feedback-manager python --version
```

最後のコマンドで Python 3.12 が表示されることを確認する。既存の `.venv` が別の Python を参照している場合は、Feedback Manager の `.venv` を再作成する必要がある。`.nix-python` と `.python-version` はマシン固有のため共有・コミットしない。Python の版を厳密に再現するには、別途 Nix の入力をロックする必要がある。

`nix build` はリンクが残っている間は再実行不要だが、別のプロジェクトに導入する場合はそのインストール先にも Python の指定が必要。システム全体で Python を恒常的に使いたい場合は、代わりに NixOS の設定に追加できる。

```nix
environment.systemPackages = with pkgs; [
  python312
];
```

NixOS の設定を反映した後、`/run/current-system/sw/bin/python3 --version` で Python 3.11 以上を確認し、対象プロジェクトのルートで Feedback Manager に指定する。

```sh
printf '%s\n' /run/current-system/sw/bin/python3 \
  > .agents/skills/feedback-manager/.python-version
```

この方法では Python 本体はシステム全体で利用でき、Feedback Manager の仮想環境は利用先プロジェクトと分かれる。システム更新によって Python の版が変わる場合がある。

連携対象は **OpenCode 1.18.33 / Codex CLI 0.159.3、POSIX シェル環境**。Windows ネイティブの起動コマンドは未対応。ホストの実行環境に uv が PATH 上で必要。

- OpenCode: プロジェクトの `.opencode/plugins/` を読み込む状態で再起動する。`chat.message` が共通 Python を実行し、synthetic text part に結果を追加する。トーストとモデルへの短い利用明示指示で呼び出しを知らせる。
- Codex: プロジェクトを信頼したうえで `/hooks` から定義をレビュー・信頼する。設定を置くだけでは信頼されない。ポリシーで非管理フックが禁止されている環境では動作しない。`UserPromptSubmit` が `additionalContext` を返し、`systemMessage` で利用を通知する。サブディレクトリからも祖先のインストールを解決する。フック取得失敗時は exit 2 で発言処理を止める。
- 両ホスト: 文脈からのタグ選択と二段階目の CLI 呼び出しはモデルに依存する。フック実行だけでは適用の正しさを保証しない。フック未提供時はスキルから `bootstrap` を呼ぶ。

対象バージョンの資料と検証範囲は [docs/verification.md](docs/verification.md) を参照。

## モードとプロファイル

通常の修正依頼からモデルが継続的な好みを抽出する。「FM に保存して」という指定は不要。抽出や意味的な判断はモデルが行い、CLI は設定に基づく読み書き制限を強制する。

プロジェクトルートの `feedback-manager.toml` を使う。インストール時には [設定例](feedback-manager.example.toml) を配置し、既存設定は更新時にも保持する。未設定時は `ask`、更新可、削除不可。

```toml
default_mode = "ask"
allow_update = true
allow_delete = false

[profiles.trusted]
mode = "auto"

[profiles.local]
mode = "read_only"

[profiles.small_context]
mode = "off"

[profiles.append_only]
mode = "auto"
allow_update = false
```

| モード | 参照 | 保存 |
| --- | --- | --- |
| `off` | 不可。フックは指針・タグ・通知を渡さない | 不可 |
| `read_only` | 可 | 不可。既存 DB は読み取り専用接続、未作成ならディスクに DB を作らない |
| `ask` | 可 | 内容を確認した変更または一括変更だけ可 |
| `auto` | 可 | 明確な継続的希望を自動保存。タグも自動分類 |

`allow_update = false` は更新・無効化・再有効化・取り消しを禁止。`allow_delete = false` は削除と、記録を除去する取り消しを禁止。どちらも `auto` や承認済みの `batch` で迂回できない。`read_only` は追加も禁止する。`off` で利用できる操作は状態確認用の `policy` と空の `bootstrap` のみ。

起動するシェル／ランチャーで `FM_PROFILE=trusted` などを設定し、その環境をホストとツール実行に継承する。モデル名からの信頼度推測や、ホスト名からの自動選択はしない。未指定は既定値、未登録プロファイル・不正設定はエラーとする。各プロファイルで省略した権限はトップレベルから継承する。

`FM_CONFIG` に絶対パスを設定すると、プロジェクト外のユーザー管理設定を利用できる。設定や環境変数を変更できるプロセスは制限を変えられるため、これは OS の隔離機構ではない。信頼できないモデルにはランチャー側で環境を固定し、設定ファイル・DB・コードへの直接書き込みも実行権限で制限する。スキルには自己判断での設定変更・制限回避を禁止している。

`auto` でも曖昧な発言、今回限りの修正、モデル自身の提案は保存しない。既存指針との矛盾は自動上書きしない。`ask` では作業の区切りで候補をまとめて確認できる。`confirmed` は呼び出し側の申告であり、CLI が会話上の承認を検証するものではない。

## 保存内容を見る

この配布元のディレクトリでは、次のコマンドだけで現在の DB を表示できる。JSON の入力は不要。

```sh
uv run --project . scripts/feedback.py --project . --view
```

導入先のプロジェクトルートでは次のコマンドを使う。

```sh
uv run --project .agents/skills/feedback-manager \
  .agents/skills/feedback-manager/scripts/feedback.py --project . --view
```

`--view` は有効な指針、`--view all` は無効を含む全指針、`--view history` は変更履歴を表示する。モード・プロファイル・件数と、指針・元発言・タグ・完全な ID・UTC 日時を Markdown で出力する。ファイルに残すなら `--view > feedback.md` のようにリダイレクトできる。履歴は変更された記録の前後を表示する。

エージェントにも「FM の保存内容を見せて」「無効な記録も含めて見せて」「変更履歴を見せて」と依頼できる。スキルは現在の DB を読み出して表示し、閲覧だけで保存・更新しない。

閲覧コマンドは読み取り専用接続を使い、DB 未作成時にも DB を作らず、旧 DB のスキーマも移行しない。read_only/ask/auto で利用でき、off は閲覧を拒否する。失敗時は標準エラーに理由を表示して終了コード1を返す。従来の JSON 入出力は `--view` を指定しなければ変わらない。

## CLI と JSON

以下を対象プロジェクトのルートで実行する。パスに空白があれば引用する。CLI は作業ディレクトリを推測せず、必須の `--project` を DB の範囲とする。

```sh
uv run --project .agents/skills/feedback-manager \
  .agents/skills/feedback-manager/scripts/feedback.py --project . <<'JSON'
{"op":"bootstrap"}
JSON
```

stdin に一つの JSON オブジェクト、stdout に一つの JSON 結果。成功は終了コード 0 と `{"status":"ok","operation":"…","data":…}`。失敗は終了コード 1 と `{"status":"error","operation":"…","error":{"code":"…","message":"…"}}`。不正引数も構造化する（`--help` は通常のヘルプ）。JSON は最大 1 MiB、重複キー・余分なフィールド・非 JSON 定数は拒否する。

| 操作 | 入力例 / 意味 |
| --- | --- |
| 登録 | `{"op":"create","guideline":"簡潔に回答する","original":"今後は簡潔に回答して","tags":{"existing":[],"create":[]},"confirmed":true}` |
| タグ新設付き登録 | 上記の tags を `{"existing":[],"create":["文書"]}` にする |
| 一覧 | `{"op":"list"}` は有効のみ。`"include_inactive":true` を追加すると無効も含む |
| ID 取得 | `{"op":"get","id":"UUID"}` は無効な記録も取得できる |
| 更新 | `{"op":"update","id":"UUID","guideline":"短く説明する","original":"今後は説明を短くして","confirmed":true}` |
| タグ変更 | `{"op":"update","id":"UUID","tags":{"existing":["文書"],"create":[]},"confirmed":true}` |
| 無効化 | `{"op":"disable","id":"UUID","confirmed":true}` |
| 再有効化 | `{"op":"update","id":"UUID","active":true,"confirmed":true}` |
| 削除 | `{"op":"delete","id":"UUID","confirmed":true}`。関連付けも削除する |
| タグ一覧 | `{"op":"tags"}` |
| 検索 | `{"op":"search","tags":["文書","テスト"]}`。既存タグの OR とタグなしの和集合、有効のみ |
| 設定確認 | `{"op":"policy"}`。有効なモード・権限・プロファイル・設定パス |
| 履歴 | `{"op":"history"}`。ID、日時、操作、変更前後の全記録・タグ |
| 取り消し | `{"op":"undo","history_id":1,"confirmed":true}`。最新履歴だけを取り消す |
| 発言時取得 | `{"op":"bootstrap"}`。`policy` に設定、`feedback` に有効なタグなし、`tags` に既存タグ一覧 |
| 一括変更 | `{"op":"batch","confirmed":true,"operations":[{"op":"update","id":"UUID1","guideline":"新方針","original":"元発言"},{"op":"disable","id":"UUID2"}]}` |

ID は生成された UUID を使用する。記録は `id, guideline, original, active, created_at, updated_at, tags` を含む。日時は UTC、元発言は前後の空白を含めて保持する。指針は 4,000 文字、元発言は 20,000 文字まで。空白のみや NUL を含む入力は拒否する。

タグは大文字小文字を区別し、前後の空白を除去する。空・重複は拒否する（自動で重複除去しない）。一配列最大100件、各100文字。`existing` の未登録タグは `UNKNOWN_TAG`、`create` の登録済みタグは `TAG_EXISTS`。タグなしは両配列を空にする。タグ内の旧 `confirmed` は互換性のため受け付けるが、承認として扱わない。`ask` の書き込みではリクエスト最上位に `"confirmed":true` が必要。更新・無効化・削除・取り消しも同様。`batch` は最上位の承認が全子操作に適用される。`auto` では承認フィールド不要。読み取り操作に承認フィールドは付けない。更新で tags を省略すると現状維持する。使われなくなったタグも一覧に残し、再利用可能とする。

batch は最大100件、create/update/disable/delete を順に一つのトランザクションで処理する。途中で失敗した場合、タグ新設を含め全変更をロールバックする。batch 内で新設したタグは後続操作で existing として使える。矛盾の意味的判定はモデルが行う。

各書き込み／batch は変更前後の全記録・タグを同じトランザクションの履歴に保存する。`undo` は `history` で確認した最新 ID を指定し、その変更全体を取り消す。変更後の状態と実 DB が異なる場合は拒否する。取り消し自体も履歴に残る。記録削除後も元発言は履歴に残るため、完全消去の用途には使わない。全件スナップショット方式なので履歴容量は記録数・変更回数に応じて増える。自動的な履歴削除は行わない。

## 保存・エラー

DB は `<project>/.feedback-manager/feedback.sqlite3`。インストーラーはプロジェクトの `.gitignore` に `/.feedback-manager/` を追加する。WAL、外部キー、5秒のロック待機を使う。スキーマ v1 は書き込み可能なモードで v2（履歴テーブル追加）へ自動移行する。read_only は v1 も変更せず参照できるが、履歴は空となる。off/read_only のエラーはプロジェクトログへ書き込まない。単一エージェント運用が前提。スキルを移動・更新しても運用データは上書きしない。別プロジェクトのデータは共有しない。

設定関連のエラーは `CONFIG_ERROR`, `FM_DISABLED`, `WRITE_FORBIDDEN`, `UPDATE_FORBIDDEN`, `DELETE_FORBIDDEN`, `CONFIRMATION_REQUIRED`, `UNDO_CONFLICT`。その他のエラーコードには `INVALID_JSON`, `INVALID_ARGUMENT`, `INVALID_INPUT`, `INVALID_OPERATION`, `INVALID_ID`, `NOT_FOUND`, `UNKNOWN_TAG`, `TAG_EXISTS`, `PROJECT_NOT_FOUND`, `SCHEMA_VERSION`, `DB_LOCKED`, `DB_ERROR`, `IO_ERROR` がある。Python は `.feedback-manager/errors.jsonl` に UTC 日時・操作・コード・固定説明を記録し、1 MiB 到達後の次回記録時に一世代 `.1` へローテーションする。日数による削除は行わない。元発言、SQL、秘密情報、例外全文は記録しない。ログも書けない場合は stderr に `LOG_WRITE_FAILED` と元のエラーを出す。

OpenCode の起動・通信エラーは同ディレクトリの `host-errors.jsonl` に内容を含まない固定説明を記録する。こちらも1 MiB・一世代で保持する。フック未実行や uv 自体の起動失敗など、Python に到達しない障害はホストのエラー表示も確認する。

## テスト

```sh
uv run --project . python -m unittest discover -s tests -v
node --test tests/opencode.test.mjs
```

Python テストは標準ライブラリのみ。ホストの実動検証は検証文書に記載し、単体テストの成功と区別する。
