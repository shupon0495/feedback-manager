"""Human-readable Markdown for inspection; stored strings remain quoted data."""
import re


def safe(text):
    # Show terminal controls literally; keep newlines for readable quotations.
    text = "".join(char if char == "\n" or char.isprintable() else ascii(char)[1:-1] for char in str(text))
    return re.sub(r"([\\`*_{}\[\]<>()#+.!|~>-])", r"\\\1", text)


def quote(text):
    return "\n".join("> " + line for line in safe(text).split("\n"))


def record_lines(row, heading="###"):
    return [
        f"{heading} {row['id']}", "",
        f"状態: {'有効' if row['active'] else '無効'}",
        "タグ: " + (", ".join(safe(tag) for tag in row["tags"]) or "タグなし"),
        f"作成: {row['created_at']} / 更新: {row['updated_at']}（UTC）", "",
        "指針:", "", quote(row["guideline"]), "",
        "元発言:", "", quote(row["original"]), "",
    ]


def render_view(project, policy, kind, data):
    title = {"active": "有効な指針", "all": "すべての指針", "history": "変更履歴"}[kind]
    lines = ["# Feedback Manager", "", "プロジェクト: " + safe(project),
             f"モード: {policy['mode']} / プロファイル: {safe(policy['profile'] or '既定')}",
             f"更新許可: {policy['allow_update']} / 削除許可: {policy['allow_delete']}", "",
             f"## {title}（{len(data)}件）", ""]
    if not data:
        lines.append("変更履歴はありません。" if kind == "history" else "保存された指針はありません。")
    elif kind != "history":
        for row in data:
            lines.extend(record_lines(row))
    else:
        for event in data:
            lines.extend([f"### 履歴 {event['id']}: {event['operation']}", "", f"日時: {event['timestamp']}（UTC）", ""])
            before = {row["id"]: row for row in event["before_state"]["feedback"]}
            after = {row["id"]: row for row in event["after_state"]["feedback"]}
            for record_id in dict.fromkeys([*before, *after]):
                old, new = before.get(record_id), after.get(record_id)
                if old == new:
                    continue
                lines.extend([f"{'追加' if old is None else '削除' if new is None else '変更'}: {record_id}", ""])
                if old:
                    lines.extend(["変更前:", "", *record_lines(old, "####")])
                if new:
                    lines.extend(["変更後:", "", *record_lines(new, "####")])
            if event["before_state"]["tags"] != event["after_state"]["tags"]:
                lines.extend(["タグ一覧の変更:", "", quote(", ".join(event["before_state"]["tags"]) or "タグなし"),
                              "", "↓", "", quote(", ".join(event["after_state"]["tags"]) or "タグなし"), ""])
    return "\n".join(lines).rstrip() + "\n"
