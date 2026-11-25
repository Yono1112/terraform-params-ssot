# パラメータシート — example

このページは `params.yaml`（唯一の正本）から `generate.py` で自動生成されています。直接編集せず、YAML を編集してください。各セルはその環境での実効値を表示します。

- ※ = 環境間で値が異なります
- （デフォルト） = その環境では未設定のため、デフォルト値が適用されます

## 全般

| パラメータ | 型 | 説明 | dev | staging | prod |
| --- | --- | --- | --- | --- | --- |
| `name_prefix` | string | すべてのリソース名に付与するプレフィックス。環境名と組み合わせて使用します。 | demo-app（デフォルト） | demo-app（デフォルト） | demo-app（デフォルト） |
| `tags` ※ | map(string) | タグ付け可能なすべてのリソースにマージされる追加タグ。 | Owner = dev-team | Owner = qa-team | Owner = platform-team |

## アプリケーション

| パラメータ | 型 | 説明 | dev | staging | prod |
| --- | --- | --- | --- | --- | --- |
| `instance_count` ※ | number | 実行するアプリケーションのインスタンス数。 | 1 | 2 | 4 |
| `enable_detailed_logs` ※ | bool | アプリケーションの詳細ログを有効にします。 | true | false（デフォルト） | false（デフォルト） |

## ネットワーク

| パラメータ | 型 | 説明 | dev | staging | prod |
| --- | --- | --- | --- | --- | --- |
| `allowed_cidr_blocks` ※ | list(string) | アプリケーションのエンドポイントへのアクセスを許可する CIDR ブロック。 | 10.0.0.0/8 | 10.0.0.0/8, 172.16.0.0/12 | 203.0.113.0/24 |

正本： params.yaml ・ 再生成： python3 generate.py ・ 乖離チェック： python3 generate.py --check
