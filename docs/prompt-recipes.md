# プロンプトレシピ

GPT-5.6には、task固有の成果、境界、検証だけを渡します。永続promptにある安全規則やrole説明を繰り返しません。

Rootは対象repoを読まない回答・説明だけをfast pathで扱います。対象repoの調査、コード・差分の読み取り、実装、validation、browserの計画/許可操作仕様化/根拠解釈、debug、review evidence収集は、次のtask promptを持つnamed roleへ委譲します。Rootはrole仕様どおりbrowser-control toolだけを実行して観測事実を記録し、reportとsnapshotから次actionを決めます。

## 実装

```text
目的: <達成する結果>
成功条件: <観測可能な条件>
対象: <target_repo_rootとscope>
許可: <read/edit/validate。Gitや外部更新は必要時だけ明示>
検証: <必須check>
non-goal: <不要な拡張>
```

## Read-only mapmaking

```text
この領域をread-onlyで調べ、実行経路、変更境界、依存、危険箇所、推奨方針、検証観点を返してください。実装や状態更新はしません。
```

## Parallel implementation

```text
独立したowned scopeだけを並列化してください。共有artifactのownerを一つにし、全result後にmutationを止め、artificerがcross-scope glueと統合検証を担当してください。
```

## Independent review

```text
この変更をread-onlyで確認してください。success criteria、scope、authority、安全、validation evidenceを共通checkとし、変更に関係するsecurity/compatibility/performance等だけ追加してください。findingは根拠と影響を示してください。
```

## Sage

```text
<具体的な独立focus>だけをread-onlyで調べ、ownerが確認できるevidence、options、risks、unknownsを返してください。実装や採否は行いません。
```

sageは具体的なfocusがある時だけ使います。使わない理由や自己評価値は不要です。

## Safety stop

```text
secret/PII、破壊的操作、依存追加、migration、deploy、本番・課金・認可・公開API互換性、外部network有効化が必要なら、実行せず必要な承認範囲を返してください。
```

## Output guidance

```text
結論を先に示し、必要な成果物、検証根拠、注意点、次の行動を保持してください。前置きと反復は削ってください。
```

汎用的な「短く」「簡潔に」だけを指定しません。必要なartifactや根拠を落とす可能性があるためです。
