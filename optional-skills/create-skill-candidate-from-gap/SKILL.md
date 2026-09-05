---
name: create-skill-candidate-from-gap
description: "検証済みで匿名化したcapability gapから、隔離されたSkill候補をexact pathへ作る時だけ使います。active Skillへのpromotionはしません。"
metadata:
  owner: agent-guild-orchestra
  scope: optional-skill-candidate
---

# create-skill-candidate-from-gap

既存Skillで満たせないbounded capability gapを、candidate-onlyの隔離状態へ変換します。明示 invocation専用です。

## 使う時

- repeated independent evidenceまたはstable prevention artifactがgapを示し、既存Skillで安全に満たせない時
- sanitized evidenceに再現条件、stable input/output、deterministic validationがある時
- ユーザーがabsolute `candidate_path`、target Git root、candidate name、candidate-only write authorityを明示した時

## 使わない時

一回限り・未検証・秘密情報・認証情報・PII・raw log、active Skill更新・install・promotion、candidate path/owner/target/validatorが不明な時。

## 手順

1. `update-existing`、`dismiss`、`new-candidate`をevidenceと既存Skill inventoryへ照合する。判断できなければ止める。
2. absolute non-symlinkの空directoryとしてcandidate pathを固定し、candidate-only authorityをSKILL.mdと`agents/openai.yaml`へ限定する。
3. 本文へtrigger、sanitized input、bounded output、authority、validation、non-goals、promotion gateだけを書く。
4. `scripts/validate_skill_candidate.py --target-repo-root ... --candidate-path ...`を実行し、active Skill、target repo、親workspace、他candidateを変更しない。
5. validator成功後もlifecycleは`needs_human`とし、promotion判断を返す。

## 出力

disposition、candidate/target path、qualification、lifecycle、validator result、content digest、未解決のpromotion判断、残るrisk。

## 安全

Git、external action、install、promotion、cleanup、rename、active Skill編集を行いません。secrets、tokens、credentials、passwords、keys、auth data、PII、raw tool outputを扱いません。validatorのdigestはsnapshotや承認の代替ではありません。

## 停止条件

qualification不足、既存Skillで解決、path collision、symlink、validator failure、authority不足、またはcandidateを作成してneeds_human gateを返せた時。
