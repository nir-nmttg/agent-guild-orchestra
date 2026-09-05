# Pull Request description

PR title/bodyは、baseとheadの実diff、関連commit、実行済みverificationから作ります。主目的、利用者影響、変更範囲、検証、未確認事項、残るriskだけを必要な粒度で含めます。

issue番号、チケットURL、性能・互換性の主張、検証済みという表現は、実差分または観測evidenceで裏付けられる時だけ書きます。不要な内部path、secret、PII、未公開情報は書きません。

PRを作成しない依頼では、titleとbodyを別のMarkdown code fenceで返します。生成だけの読み取り作業はremoteへのpushやGitHub更新を行いません。
