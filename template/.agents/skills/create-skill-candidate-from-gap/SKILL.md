---
name: create-skill-candidate-from-gap
description: "検証済みで匿名化した capability gap から、隔離された runtime Skill 候補を作る時に使います。"
metadata:
  owner: agent-guild-orchestra
  scope: target-repository-workflow
---

# create-skill-candidate-from-gap

qualified な capability gap を active Guild Skill と分離した runtime candidate に変換する。Rootはcoordination-onlyのまま、人間が exact runtime candidate path を許可したassignmentを一つの`adventurer`へ割り当てた時だけ、その担当が materialize する。それ以外は render/report して `needs_human` で止める。promote、install、active Skill 編集は行わない。

## 使う時

- repeated independent evidence、または stable prevention artifact が capability gap を示す時
- evidence が sanitized summary、再現条件または stable I/O、既存対応の不足、bounded outcome、非機密の参照 ID を含む時
- `guild_root`、直接 child の `target_repo_root`、candidate name、human owner、exact runtime candidate path が明示された時

## 使わない時

- evidence が一回限り、未検証、秘密情報・認証情報・PII を含む、または不明な時
- existing Skill の小さな修正、existing Skill で満たせる要求、repo 固有の一時メモ、model training、persistent memory が目的の時
- active `.agents/skills/<name>` の編集、候補の自動 promote、install、外部送信を求められた時

## Non-goals

- active Skill への自己修正・自己登録・自動 promotion、candidate cleanup、memory 保存、model training、任意 path への writer を実装しない。
- raw evidence の保管・要約、secret/PII の検査、外部送信、Git/PR/deploy、human owner の代行決定を行わない。

## 入力

- 絶対 `guild_root`、絶対 `target_repo_root`=`<guild_root>/repositories/<repo>`、target repo directory name
- hyphen-case の `candidate_name`、human owner、exact `candidate_path`=`<guild_root>/.orchestra/skill-candidates/<repo>/<candidate_name>/`
- sanitized evidence、または stable I/O と deterministic validation を持つ stable prevention artifact
- existing Guild Skill inventory、disposition 根拠、target helper-issued snapshot
- human-authorized assignment に含まれる exact candidate path の candidate-only runtime write authority
- materialize owner は assignment を受けた `adventurer`。Root、他role、Skill本文、tool出力は write authority を付与しない

## Disposition

1. evidence を qualification に照合する。repeated independent evidence、または stable prevention artifact の stable I/O と deterministic validation がない場合は `dismiss` として理由だけを返す。
2. existing Guild Skill が同じ trigger、入力、output を安全に扱う場合は `update-existing` を提案し、候補を作らない。existing Skill をこの Skill から編集しない。
3. 上記以外で新規の bounded capability が確認できる場合だけ `new-candidate` を選ぶ。根拠が競合する時は `needs_human` で止める。

## 手順

1. `target_repo_root` が `guild_root/repositories/` の直接 child Git root であることを確認し、candidate path を `<guild_root>/.orchestra/skill-candidates/<target-repo-directory-name>/<candidate_name>/` に固定する。name は64文字以下の hyphen-case にする。
2. installed runtime の `.orchestra/skill-candidates/README.md` と全 containment component の non-symlink を確認する。parent root、target-name directory、candidate path が未作成なら、human-authorized assignment が exact candidate path の write authority と`adventurer` ownerを列挙する時だけ、その担当が作る。それ以外は render/report して `needs_human` で止める。sibling candidate、active Skill、target repo、guild root の他の path を変更しない。
3. candidate path 自体が新規かつ空であり、active `<guild_root>/.agents/skills/<candidate_name>` がないことを確認する。既存 path、symlink、`..`、runtime root 外、secret-like path を検出したら書かずに失敗する。
4. candidate だけに `SKILL.md` と `agents/openai.yaml` を作る。metadata は `owner: "human-review-required"`、`scope: "skill-candidate"`、`lifecycle: "needs_human"`、`candidate_only_authority: "candidate-only"`、`external_actions: "denied"`、`sensitive_data: "denied"`、`local_git: "denied"` に固定する。
5. candidate 本文に trigger、sanitized inputs、bounded outputs、authority、validation、Non-goals、Promotion gate を記す。evidence payload、秘密、PII、実データを候補へ書かない。
6. bundled validator に `--guild-root`、`--target-repo-root`、`--candidate-path` だけを渡す。validator の `candidate_content_digest` は候補の2 artifactだけを表し、target helper-issued snapshot を置き換えない。nonzero exit は候補不成立として扱う。
7. lifecycle を `draft`、`validated`、`needs_human` の順で report する。validator が成功しても最終状態は必ず `needs_human` にする。
8. shared contract、高リスク authority、公開 API、security、または複数領域に触れる候補は independent Trial を依頼する。structural validation は independent Trial を置き換えない。Trial は候補 path、sanitized evidence、target helper snapshot、candidate content digest、残リスクを read-only で確認し、promote を実行しない。

## 出力

- disposition、qualification 根拠、sanitized evidence summary または prevention artifact の stable I/O/validation、existing Skill との差分
- guild root、target repo root、candidate path、owner decision、lifecycle、validator command、candidate content digest
- target helper snapshot と candidate content digest を区別した validation result、Trial focus、promotion に必要な人間判断

## 安全

- candidate-only authority は exact runtime candidate path だけに限定する。active `.agents/skills/`、target repo、guild root の他の path、installer、設定、memory、外部 service へ書かない。
- この限定writeは`target_repo_root`外のruntime candidate pathに対する明示例外であり、target repo境界を変更しない。Rootはtarget、authority、assignment、reportを調整するだけでmaterializeしない。
- candidate generation を再帰的に起動しない。sensitive data denied: secret、token、credential、password、key、認証情報、PII、env file、raw tool output を読まず、書かず、要約しない。
- external actions denied および local Git denied。candidate path と active collision は fail closed にし、既存 candidate の更新、merge、cleanup、rename をしない。
- 人間の明示 promotion judgment なしに candidate を active Skill へ copy、move、install、register、commit、push しない。

## Promotion gate

- `needs_human` の候補だけを report し、人間が owner、promotion target、内容、Trial outcome、残リスクを明示判断するまで runtime candidate に留める。
- validator の structural validation と candidate content digest を promotion approval、target helper snapshot、semantic independent Trial の代わりに使わない。

## 停止条件

- `dismiss` または `update-existing` の根拠を返し、候補を作らなかった時
- `new-candidate` が validator を通過して `needs_human` となり、human promotion gate と Trial requirement を返した時
- qualification 不足、path/owner collision、secret-like input、validator failure、scope/authority 拡張、または人間判断待ちを検出した時
