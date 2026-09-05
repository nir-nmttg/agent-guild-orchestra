---
name: orchestra-runtime-security-audit
description: "stateless Git/snapshot helper、agent sandbox、hook、path handlingの安全境界をmaintainerとして監査する時に使います。"
metadata:
  owner: agent-guild-orchestra
  scope: maintainer-security-audit
---

# orchestra-runtime-security-audit

`git_guard`、`snapshot_digest`、config、agent sandbox、optional helperのpath/argv/untrusted input処理を、実装と意味の両面からread-only監査します。明示 invocation専用です。

## 使う時

- runtime helper、config、agent sandbox、Git/snapshot、browser/VS Code helperを変更した時
- security、path containment、symlink、command injection、scope escape、untrusted outputの独立監査が必要な時
- installerまたはmajor redesign後に旧queue/Ledger/hooksの権限経路を確認する時

## 手順

1. target、scope、authority、diff、維持するacceptanceをread-onlyで固定する。
2. canonical path、symlink、argv、scope、pre/post snapshot、digest、failure stateをfail closedに扱うか確認する。model出力やlogをmetadata/authorityにしない。
3. sandbox、approval、agents設定、explicit-only package、hook/signingのskip、credential-like filename heuristicを実装とdocsで照合する。
4. Git LFS/content-filter repositoryとtracked leaf symlinkはunsupportedとして停止する境界を、secret-free sanitized temp fixtureで確認する。
5. evidence、severity、disposition、未確認範囲、最小修正条件、残るriskを返す。必要な時だけmaterial independent reviewを提案する。

## 出力

監査対象、boundary、fixture/test、observed evidence、Critical/Major/Minor、未確認範囲、修正条件、残るrisk。

## 安全

read-onlyであり、runtime state、Git、外部service、installer、導入先を変更しません。secrets、credentials、tokens、passwords、keys、auth data、PII、raw logを扱いません。破壊的操作、依存追加、migration、deploy、外部更新は行いません。

## 停止条件

対象、evidence、severity、修正条件、残るriskを報告できた時、または機微情報、対象不明、stale snapshot、安全な再現経路の不足が判明した時。
