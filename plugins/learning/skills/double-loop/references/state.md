# 設定と状態の形式

設定と状態は, XDG Base Directory に従って対象ごとに分けて置く. 統治ファイルを書き換えた結果はここに置かず, 統治ファイルそのものに現れる. JSONL は1行1件である.

```
$XDG_CONFIG_HOME/double-loop/<対象名>.json    設定ファイル (既定 ~/.config)
$XDG_STATE_HOME/double-loop/<対象名>/         状態ディレクトリ (既定 ~/.local/state)
  ledger.jsonl
  runs/<YYYY-MM-DD>/
    run.json
    governing.jsonl
    governing.diff.jsonl
    events.jsonl
    judgments.jsonl
    analysis.md
    proposals.jsonl
    patches/<id>-<n>.patch
    report.md
```

## 設定ファイル

初回に作り, 以後はこの値だけを使う. 利用者が `cwds` や `min_exposure` を書き換えてよい.

- `cwds`: 対象の cwd の配列. worktree も含める. Codex の記録は, この下の cwd で始めたセッションも対象にする.
- `claude_dir`: Claude Code の設定ディレクトリ. 記録は `projects/` の下にある. 見つからなければ `null`.
- `codex_home`: Codex の設定ディレクトリ. 記録は `sessions/` の下にある. 見つからなければ `null`.
- `min_exposure`: 採用後の判定に要る, 曝露したセッションの数. 既定は3.

## ledger.jsonl

採用された改訂を, 採用を検出した run で1件ずつ足す. 書き換えるのは `status` と `decided_at` だけである.

- `id`: 由来の提案の `id`. 提案に由来しない改訂は `manual-<日付>-<連番>`.
- `source`: 改訂か差し戻しの提案なら, 元の改訂の `id`. 新しい提案なら `proposal`, 人の改訂なら `manual`.
- `target`: 提案の `target` と同じ形.
- `signature`: 提案の `signature` と同じ形.
- `adopted_at`: 採用日. UTC の `YYYY-MM-DDTHH:MM:SSZ`.
- `status`: `tracking`, `revised`, `reverted` のどれか. `revised` と `reverted` は, その改訂を改める提案が採用されたときに付け, 以後は追跡しない.
- `decided_at`: `status` を書き換えた時刻.

## run.json

- `since`, `until`: この run が対象にする出来事の時刻の範囲. `since` は前回の run の `until` で, 初回は `1970-01-01T00:00:00Z`.
- `prev`: 前回の run のディレクトリ. 初回は空.

## governing.jsonl

統治ファイルの空でない行を1件とする. 行の同一性は `file` と `text` で決め, 行番号は表示にだけ使う.

- `scope`: `user`(すべての対象に効く), `project`, `path`(`paths:` を持つ rule).
- `layer`: `text`(`CLAUDE.md`, `AGENTS.md` など), `rule`, `skill`, `hook`, `permission`, `config`.
- `file`: 絶対パス.
- `line`: 行番号. JSON の settings から取った行は0.
- `text`: 行の文. permission は `<allow|ask|deny> <規則>`, hook は `<event> [<matcher>] <command>` とする.

`governing.diff.jsonl` は, 前回の snapshot との差分であり, 各件に `change`(`+` か `-`)を足したものである.

## events.jsonl

- `cli`: `claude` か `codex`.
- `cwd`: セッションの cwd.
- `session`: セッションの id.
- `path`, `line`: 元の記録のファイルと行番号. 引用はこの2つで指す.
- `ts`: 記録の時刻.
- `kind`: `prompt`, `interrupt`, `rejected`, `denied`, `failed`, `tool`.
- `tool`: `kind` が `tool` のときだけある. `shell`, `edit`, `read`, またはツール名.
- `text`: 抜粋. 長さを切り, 秘密らしい文字列を `[REDACTED]` にしたもの.

## judgments.jsonl

追跡中の改訂と, 曝露したセッションの組ごとに1件とする. 判定済みの組は書き直さない.

- `change`: 改訂の `id`.
- `session`: セッションの id.
- `recurred`: 曝露のあとで同じ逸脱が起きたなら `true`.
- `cite`: 再発の根拠にした出来事の `path` と `line`. 再発していなければ省く.
- `note`: 判定の理由.

## proposals.jsonl

検査を通った提案だけを残す. 取り下げた提案は消す.

- `id`: `<YYYY-MM-DD>-<連番>`.
- `op`: `ADD`, `EDIT`, `MOVE`, `MERGE`, `DEPRECATE`.
- `layer`: 改訂後の行の層. `governing.jsonl` の `layer` と同じ値.
- `tightens`: `EDIT` で規則を強めるなら `true`.
- `source`: 追跡中の改訂を改めるか差し戻す提案なら, その改訂の `id`.
- `target`: ファイルごとの差分の配列. 各件は `file`, `scope`, `before`(消える行の配列), `after`(現れる行の配列)を持つ. `ADD` は `before` が空で, `DEPRECATE` は `after` が空である. 行の文は `governing.jsonl` の `text` と同じ形にする. 文書なら統治ファイルの1行そのもので, settings の JSON なら `<allow|ask|deny> <規則>` か `<event> [<matcher>] <command>` である. 採用の検出はこの形で照合する.
- `trigger`: `recurrence`(行があるのに再発), `assumption`(行に従ったのに訂正), `conflict`(行どうし, または訂正と行の矛盾), `missing`(行がないまま訂正が反復), `tracking`(追跡の判定による改訂か差し戻しか復活).
- `assumption_before`: 今の行が暗に置いている前提.
- `assumption_after`: 改訂後の行が置く前提.
- `evidence`: 引用の配列. 各件は `path`, `line`, `quote`(その出来事の `text` に逐語で含まれる文)を持つ.
- `signature`: `tool`(`shell`, `edit`, `read`, `prompt`)と `pattern`(jq の正規表現). 改訂後の行が当てはまる状況を指す.
- `check`: 強制層の提案だけが持つ. hook を動かした結果か, deny の規則が引用したコマンドに当たることを確かめた結果.

## analysis.md と report.md

`analysis.md` には, 出来事ごとに対応づけた統治行(または行なし), 分類(単ループか, 二重ループならどの契機か), 前提の問い直しを書く. `report.md` には, 追跡の判定(改訂ごとの `before` と `after` のセッション数と再発数, 保留の理由), 提案ごとの契機と前提と patch の場所, 記録が消えた期間, まだ行っていない工程を書く. 変更なしで終えた run では, `report.md` に変更なしとだけ書く.
