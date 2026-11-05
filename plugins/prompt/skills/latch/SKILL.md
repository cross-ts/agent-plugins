---
name: latch
description: |
  指示文書(AGENTS.md, SKILL.md, システムプロンプト)を, 命題内容を固定したまま発話行為の5類で書き分けた候補に展開し, Claude HaikuとGPT Lunaで並列実行した振る舞いを盲検採点してラッチ方式で最良版を選ぶ.
  ユーザーがプロンプト, AGENTS.md, スキルの改善や比較検証を求めた場合に使用する.
---

# Latch

## 目的

私は, 指示文書(AGENTS.md, SKILL.md, システムプロンプト)を, 候補間の振る舞い比較によって改善する.

## 命題の抽出

私は, 既存文書があればその内容を, なければユーザーの要件を, 番号付きの命題リスト p として `p.md` に書き出す. 私は1つの命題に1つの規則または事実だけを入れる. 候補を書き終えたら, 私は候補ごとに各命題の有無を示す対応表を `p.md` に付け, p にない限定や言い換えによる追加を削る.

## 5類の書き分け

私は p を固定し, 発話内力だけを変えた5候補を次の表に従って作り, 各候補のすべての文をその類で書く. 宣言型の候補では, 私はペルソナ宣言ではなく用語と完了条件の定義を用いる.

| 類 | 書き方 | 例 |
|---|---|---|
| 断言型 | 事実を記述し, 行動は推論に委ねる | テストは `make test` で実行される |
| 指令型 | 命令で行動を規定する | `make test` を実行せよ |
| 拘束型 | エージェントの一人称の約束として書く | 私は変更のたびに `make test` を実行する |
| 表出型 | 価値や態度を表明する | 私たちはテストの通る変更を好む |
| 宣言型 | 用語, 完了条件, 役割を定義する | 本リポジトリで完了とは `make test` が通った状態を指す |

## 交絡の統制

私は全候補で命題の集合を同一に保つ. 私は候補間の文字数の差を±15%以内に収め, 言語と見出し構成を揃える. 私は強調表現を p に含まれる分だけに留め, 否定形の数を候補間で揃える.

## 対照条件

私は文書なしの条件を比較に含める. 既存文書があれば, 私はその原文も条件に加える.

## 評価課題と採点基準

私は実行前に, 文書の目的を反映した評価課題を2つ以上と, 出力から観察できる合否判定項目を `rubric.md` に定める. 私は次節のラッチ規則と採用判定の計算方法も `rubric.md` に書く.

## 実行

私は各条件を Claude Haiku と GPT Luna でそれぞれ3回以上, 並列に実行する. 私は実行ごとに空の作業ディレクトリを用意し, 次のコマンドでユーザーレベルの指示ファイル(CLAUDE.md, AGENTS.md)を読み込ませないようにする.

```bash
# GPT Luna 用の隔離した CODEX_HOME (auth.json のみ)
ISOLATED_CODEX_HOME="$(mktemp -d)"
ln -s "${CODEX_HOME:-$HOME/.codex}/auth.json" "$ISOLATED_CODEX_HOME/auth.json"

# Claude Haiku (空の作業ディレクトリで実行)
claude -p --model haiku --setting-sources project --strict-mcp-config --no-session-persistence < prompt.txt > out.txt

# GPT Luna (空の作業ディレクトリで実行)
CODEX_HOME="$ISOLATED_CODEX_HOME" codex exec --ephemeral --skip-git-repo-check -m gpt-6-luna -s workspace-write -o out.txt - < prompt.txt
```

## 盲検採点

私は条件名を伏せたIDで出力を集め, rubric に沿って採点する.

## ラッチ規則

私は, 平均得点が最高の候補が現在保持している版(初回は対照条件のうち得点の高い方)を実行間のばらつきを超えて上回るときだけ, その候補を採用する. それ以外の場合, 私は保持版を据え置く. モデル間で勝者が分かれたら, 私は両方の結果を報告する.

## 次ラウンド

私は採用した候補を起点に, 義務の強さ, 極性, 丁寧さ, Griceの量のうち1軸だけを変えた変異を作り, 実行からラッチ規則までを繰り返す. 改善が止まるか予算に達したら, 私は終了する. 第1ラウンドの準備の時点で, 私は次ラウンドで動かす1軸の候補とその理由を `results.md` に書いておく.

## 出力

私は次の構成で成果物を残す.

```text
p.md          命題リスト
variants/     A_assertive.md, B_directive.md, C_commissive.md, D_expressive.md, E_declarative.md, baseline_none.md, baseline_original.md
rubric.md     評価課題と合否判定項目
run.sh        実行スクリプト
results.md    条件別, モデル別の得点表, 採用版, 効いた軸
```
