# Feedback Manager 0.1

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
| 登録 | `{"op":"create","guideline":"簡潔に回答する","original":"今後は簡潔に回答して","tags":{"existing":[],"create":[],"confirmed":true}}` |
| タグ新設付き登録 | 上記の tags を `{"existing":[],"create":["文書"],"confirmed":true}` にする |
| 一覧 | `{"op":"list"}` は有効のみ。`"include_inactive":true` を追加すると無効も含む |
| ID 取得 | `{"op":"get","id":"UUID"}` は無効な記録も取得できる |
| 更新 | `{"op":"update","id":"UUID","guideline":"短く説明する","original":"今後は説明を短くして"}` |
| タグ変更 | `{"op":"update","id":"UUID","tags":{"existing":["文書"],"create":[],"confirmed":true}}` |
| 無効化 | `{"op":"disable","id":"UUID"}` |
| 再有効化 | `{"op":"update","id":"UUID","active":true}` |
| 削除 | `{"op":"delete","id":"UUID"}`。関連付けも削除する |
| タグ一覧 | `{"op":"tags"}` |
| 検索 | `{"op":"search","tags":["文書","テスト"]}`。既存タグの OR とタグなしの和集合、有効のみ |
| 発言時取得 | `{"op":"bootstrap"}`。`feedback` に有効なタグなし、`tags` に既存タグ一覧 |
| 一括変更 | `{"op":"batch","operations":[{"op":"update","id":"UUID1","guideline":"新方針","original":"元発言"},{"op":"disable","id":"UUID2"}]}` |

ID は生成された UUID を使用する。記録は `id, guideline, original, active, created_at, updated_at, tags` を含む。日時は UTC、元発言は前後の空白を含めて保持する。指針は 4,000 文字、元発言は 20,000 文字まで。空白のみや NUL を含む入力は拒否する。

タグは大文字小文字を区別し、前後の空白を除去する。空・重複は拒否する（自動で重複除去しない）。一配列最大100件、各100文字。`existing` の未登録タグは `UNKNOWN_TAG`、`create` の登録済みタグは `TAG_EXISTS`。タグなしは両配列を空にする。`confirmed` はユーザー確認の呼び出し側の申告で、Python が会話内容を検証することはできない。更新で tags を省略すると現状維持する。使われなくなったタグも一覧に残し、再利用可能とする。

batch は最大100件、create/update/disable/delete を順に一つのトランザクションで処理する。途中で失敗した場合、タグ新設を含め全変更をロールバックする。batch 内で新設したタグは後続操作で existing として使える。矛盾の意味的判定はモデルが行う。

## 保存・エラー

DB は `<project>/.feedback-manager/feedback.sqlite3`。インストーラーはプロジェクトの `.gitignore` に `/.feedback-manager/` を追加する。WAL、外部キー、5秒のロック待機を使う。単一エージェント運用が前提。スキルを移動・更新しても運用データは上書きしない。別プロジェクトのデータは共有しない。

エラーコードには `INVALID_JSON`, `INVALID_ARGUMENT`, `INVALID_INPUT`, `INVALID_OPERATION`, `INVALID_ID`, `NOT_FOUND`, `UNKNOWN_TAG`, `TAG_EXISTS`, `TAG_CONFIRMATION_REQUIRED`, `PROJECT_NOT_FOUND`, `SCHEMA_VERSION`, `DB_LOCKED`, `DB_ERROR`, `IO_ERROR` がある。Python は `.feedback-manager/errors.jsonl` に UTC 日時・操作・コード・固定説明を記録し、1 MiB 到達後の次回記録時に一世代 `.1` へローテーションする。日数による削除は行わない。元発言、SQL、秘密情報、例外全文は記録しない。ログも書けない場合は stderr に `LOG_WRITE_FAILED` と元のエラーを出す。

OpenCode の起動・通信エラーは同ディレクトリの `host-errors.jsonl` に内容を含まない固定説明を記録する。こちらも1 MiB・一世代で保持する。フック未実行や uv 自体の起動失敗など、Python に到達しない障害はホストのエラー表示も確認する。

## テスト

```sh
uv run --project . python -m unittest discover -s tests -v
node --test tests/opencode.test.mjs
```

Python テストは標準ライブラリのみ。ホストの実動検証は検証文書に記載し、単体テストの成功と区別する。
