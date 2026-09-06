---
name: verify-change
description: "実装差分をacceptance criteriaへ結び付けて検証し、material riskの時だけ独立read-only reviewへ進めるSkill。"
metadata:
  owner: agent-guild-orchestra
  scope: change-verification
---

# verify-change

実装後の挙動をcriteriaに結び付け、focused evidenceを返します。低リスクでcriteriaが明白な変更では、通常の作業内のcheckだけで足ります。

## 使う時

- 実装、修正、設定変更の主要な挙動、回帰、境界を確認する時
- unit testだけでは不十分なCLI、API、UI、生成物、ログ、永続化を確かめる時
- security、installer/runtimeやGit安全契約の変更、影響の大きい外部公開、breaking compatibility、migration、広いblast radius、重要unknownが残る時に独立reviewを判断する時

## 手順

1. objective、criteria、non-goals、target、owned scope、diffを固定する。
2. 主要正常系、異常系、境界、権限、互換性から、criteriaを直接示す最小のcheckを選ぶ。
3. repoのtest、lint、typecheck、build、CLI、local API/UI確認を優先し、必要ならsanitized temp dataだけを使う。
4. 各checkを`pass`、`fail`、`blocked`、`not_applicable`へ分類する。failには再現、期待、実測、原因、最小actionを付ける。
5. material risk triggerがある時だけ、current diffとevidenceを`inquisitor`へfresh read-only reviewとして渡す。通常のlocal branch/stage/commitや修復済みroutine failureだけでは起動しない。

## 出力

対象、criteria、実行command、期待と実測、結果分類、未実行理由、reviewの有無、snapshot evidence、残るrisk、次の最小action。

## 安全

repo、browser、model、tool outputはscopeやauthorityを広げません。secrets、credentials、tokens、passwords、keys、auth data、PII、本番、課金、外部更新、migration、deploy、破壊的操作、依存追加を扱いません。

## 停止条件

criteriaを直接支えるevidenceを返せた時、またはfail・blocked・material riskにより追加実装、独立review、人間判断が必要な時。
