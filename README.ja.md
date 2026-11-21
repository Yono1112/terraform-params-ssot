[English](README.md) | [日本語](README.ja.md)

# terraform-params-ssot

**Terraform モジュールの環境別パラメータを、YAML のパラメータシート1枚で一元管理（SSOT）するツール** — そのシートから `.tfvars` ファイルと人間向けの HTML ビュワーを生成し、バイト単位の乖離チェックで CI に組み込めます。

![concept](https://img.shields.io/badge/SSOT-params.yaml-blue)

```
params.yaml ──► generate.py ──► environments/<env>.tfvars   （Terraform 用）
 （触るのは      │              viewer/params.html           （人間用）
  このファイル   └─ variables.tf と照合（乖離検知）
  だけ）
```

## なぜ作ったか

インフラのパラメータシートはスプレッドシートで管理され、実際の値は `.tfvars` ファイルの中にある、という状態になりがちです。この2つは少しずつズレていき、シートの記述とコードの実態が食い違っても、障害レビューの時まで誰も気づきません。このツールは「2つ目のコピー」を無くします。

- **値が存在する場所は YAML シートだけ。** tfvars ファイルと HTML ビュワーは*生成物*です。決定的（バイト単位で再現可能）に生成され、手で編集することはありません。
- **値の変更は機械的な作業。** どの環境の値を変えるのも、YAML の1行編集＋スクリプト実行だけ。コード変更も、エージェントも、判断も不要なので、ズレようがありません。
- **コードを触るのはスキーマ変更だけ。** *新しい*パラメータを追加する時だけ、人間（またはコーディングエージェント）が `variables.tf` に変数を追加し、リソースへ配線し、シートに1件追加します。コードに触れるのは意図的にこのステップだけに絞っています。
- **乖離は「起きないはず」ではなく検知する。** `generate.py --check` は全てを再生成してコミット済みファイルとバイト比較します。さらに生成時にシートと `variables.tf` を照合します（パラメータが存在するか、型・説明・デフォルトが同期しているか、tfvars で値が入っているのにシートに無い変数はないか）。pre-commit や CI で回せば、乖離はビルド失敗として表に出ます。

## シート：`params.yaml`

```yaml
environments:            # 環境名はここで定義 — コード側にハードコードなし
  - dev
  - prod

groups:                  # ビュワー用の日本語ラベル（任意）
  Lambda:
    label_ja: Lambda

parameters:
  - name: lambda_memory_size     # variables.tf の変数名と一致させる
    group: Lambda
    type: number                 # string | number | bool | map(string) | list(string) | any
    description: "Lambda memory size in MB (128 - 10240)."
    description_ja: "Lambda のメモリサイズ（MB、128〜10240）。"   # 任意；ビュワー専用
    default: 256                 # variables.tf 側のデフォルト値
    values:                      # 環境ごとの上書き値
      dev: 128
      prod: 512                  # 記載のない環境は `default` が使われる
```

フォーマットは `params.schema.json`（JSON Schema、draft 2020-12）で検証されます。動作するシートと生成物一式は [`example/`](example/) を参照してください。

## 使い方

`generate.py` と `params.schema.json` をモジュールにコピーし（例：`tools/` 配下）、`variables.tf` の隣に `params.yaml` を置いてから：

```bash
# tfvars とビュワーを再生成
python3 generate.py --tf-dir path/to/module

# 乖離ゲート：コミット済みの生成物がシートと食い違えば exit 1
python3 generate.py --tf-dir path/to/module --check
```

### ワークフロー

1. `params.yaml` を編集する — 編集するのは `params.yaml` だけ。
2. `generate.py` を実行する。
3. `params.yaml`、再生成された `environments/*.tfvars`、`viewer/params.html` を**一緒に**コミットする。

### CLI オプション

| オプション | デフォルト | 意味 |
|---|---|---|
| `--tf-dir DIR` | `.` | Terraform モジュールのディレクトリ |
| `--params PATH` | `<tf-dir>/params.yaml` | SSOT シート |
| `--schema PATH` | `params.yaml` の隣 | JSON Schema ファイル（無ければスキップ） |
| `--variables PATH` | `<tf-dir>/variables.tf` | 照合に使う変数定義ファイル |
| `--tfvars-dir DIR` | `<tf-dir>/environments` | tfvars の出力先 |
| `--viewer PATH` | `<tf-dir>/viewer/params.html` | ビュワー HTML の出力先 |
| `--title NAME` | `<tf-dir>` のディレクトリ名 | ビュワーに表示するプロジェクト名 |
| `--check` | — | 検証のみ；乖離があれば exit 1 |

環境名・グループラベル・パラメータ集合はすべてシートから読みます。ツール側にモジュール固有の知識はありません。tfvars の整形は `terraform fmt` の整列ルールに従うため、生成ファイルは fmt クリーンな状態を保ちます。

## 乖離ゲート

### pre-commit

[pre-commit](https://pre-commit.com) フレームワークを使う場合：

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: params-ssot-check
        name: params.yaml SSOT drift check
        entry: python3 tools/generate.py --check
        language: system
        pass_filenames: false
        files: ^(params\.yaml|variables\.tf|environments/|viewer/)
```

通常の git フックでも可：

```bash
#!/bin/sh
# .git/hooks/pre-commit
python3 tools/generate.py --check || {
  echo "params.yaml drift: run generate.py and commit the results" >&2
  exit 1
}
```

### GitHub Actions

```yaml
name: params-ssot
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pyyaml jsonschema
      - run: python3 tools/generate.py --check
```

## 要件

- Python 3.10+
- [PyYAML](https://pypi.org/project/PyYAML/)（必須）
- [jsonschema](https://pypi.org/project/jsonschema/)（任意） — 未インストールの場合、`generate.py` は `params.schema.json` と等価の内蔵構造チェックを実行し、その旨を表示します。

ジェネレータとチェックの実行に Terraform 本体は不要です。

## サンプル

[`example/`](example/) は自己完結したデモモジュールです。5パラメータ（string / number / bool / `map(string)` / `list(string)`）× 3グループ × 3環境のシート、その `variables.tf`、生成された `environments/*.tfvars` と `viewer/params.html` が入っています。ビュワーをブラウザで開いてから、次を試してみてください：

```bash
cd example
python3 ../generate.py --tf-dir .          # 再生成
python3 ../generate.py --tf-dir . --check  # 乖離ゲート
```

## 由来

実在の Terraform モジュール（dev / stg / prod で運用する Kinesis → Lambda → S3 → SQS パイプライン）から切り出したツールです。そのモジュールでは、このシートが3環境すべてのパラメータの正本になっています。
