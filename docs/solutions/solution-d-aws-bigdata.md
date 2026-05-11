# Solution D — AWS Big Data + Streaming

> **Status:** Architecture-only. Not implemented in this submission.
> **Positioning:** Cloud-native big-data + streaming reference architecture on AWS managed services. Worth choosing when the organisation has already standardised on AWS, when ten-thousand-dealer scale is the near-term target, and when the team prefers managed building blocks over self-hosted OSS.

---

## Premise

Solution A is the JD-native answer. Solution B is OSS portability. Solution C is Microsoft enterprise integration. Solution D is **the AWS reference architecture** — a composition of managed services that has been deployed at scale across the industry for catalog ingestion, real-time inventory propagation, and analytical reporting.

Where Solution B uses Polars + Iceberg + Dagster as portable OSS, Solution D uses **S3 + Glue Catalog + Iceberg + Kinesis + MSK + Lambda + Step Functions + Athena + Redshift + DynamoDB + DMS**, all managed by AWS. The trade-off is direct: AWS lock-in in exchange for the ability to scale to ten thousand dealers on a paved road, with security, observability, and DR primitives that exist out of the box rather than as integration projects.

---

## Stack

| Layer                              | AWS service                                         | Role                                                                  |
| ---------------------------------- | --------------------------------------------------- | --------------------------------------------------------------------- |
| Object storage / lakehouse         | **Amazon S3** + **Apache Iceberg**                  | All data lives here; Iceberg metadata provides ACID + time-travel    |
| Catalog                            | **AWS Glue Data Catalog**                            | Schema registry for Iceberg tables; consumed by Athena, Redshift, EMR|
| Batch transformations              | **AWS Glue Jobs** (Spark, serverless)                | Bronze → silver → gold; auto-scales workers                          |
| Lightweight serverless ETL         | **AWS Lambda**                                      | Per-file triggers, per-event handlers, sub-15-minute jobs            |
| Workflow orchestration             | **AWS Step Functions**                              | Generic state machine reads dealer_bindings from DynamoDB, dispatches |
| Streaming ingestion                | **Amazon Kinesis Data Streams**                      | Inbound webhooks → partitioned event log                              |
| Streaming delivery                 | **Kinesis Data Firehose**                           | Auto-batched delivery from Kinesis to S3 / Iceberg                   |
| Managed Kafka (alternative)        | **Amazon MSK** (or **MSK Serverless**)               | For teams already on Kafka API; full Confluent ecosystem              |
| CDC                                | **AWS DMS** (Database Migration Service)             | PostgreSQL logical replication slot → Kinesis or MSK                  |
| Streaming SQL                       | **Amazon Managed Service for Apache Flink**          | Stream processing with state, joins, windows                          |
| Ad-hoc analytical queries          | **Amazon Athena**                                    | Standard SQL over Iceberg tables on S3, pay-per-query                 |
| Warehouse + BI                      | **Amazon Redshift** + **Redshift Spectrum**          | Sub-second BI over Iceberg without copy                               |
| Serving layer (hot)                | **Amazon DynamoDB**                                  | Single-digit ms reads for catalog API + marketplace listings         |
| Serving layer (relational)         | **Amazon RDS for PostgreSQL** + read replicas        | Compatibility with Solution A; transitional                          |
| Event routing                      | **Amazon EventBridge**                              | Cross-service event bus; SaaS-event triggers                          |
| Compute (containers)               | **Amazon ECS on Fargate** (or **EKS**)                | Long-running workers + API services                                   |
| Compute (functions)                | **AWS Lambda**                                       | Stateless handlers; 15-minute max execution                          |
| Read API                            | **Amazon API Gateway** + **Lambda** (or **AppSync**) | REST + GraphQL endpoints with managed auth + throttling              |
| Identity                            | **AWS IAM** + **Cognito** (or **Entra ID federation**)| Per-dealer roles, federated SSO                                       |
| Secrets                             | **AWS Secrets Manager** + **KMS**                    | Per-environment secrets with rotation                                 |
| Observability                       | **CloudWatch** + **X-Ray** + **OpenSearch**          | Logs, traces, dashboards, alarms                                      |
| Cost tracking                       | **AWS Cost Explorer** + **Budgets**                  | Per-service, per-tag cost attribution                                 |
| Infrastructure as code              | **Terraform** or **AWS CDK**                          | Declarative provisioning                                              |
| Marketplace sync (outbound)        | **AWS AppFlow** (or custom via Step Functions)        | Managed connectors to Salesforce, SAP, marketplaces                  |
| ML / embeddings                     | **Amazon Bedrock** (Claude, Llama, Titan)             | LLM translation; same ILLMProvider abstraction surface                |
| Search                              | **Amazon OpenSearch Service**                         | Full-text search over catalog (multi-language)                       |
| Edge CDN                            | **Amazon CloudFront**                                 | Schematic image distribution                                          |

Every component is a managed service. There is no Kubernetes cluster to operate (unless EKS is preferred), no Kafka brokers to patch (MSK Serverless handles it), no Iceberg metadata service to deploy (Glue Catalog provides it).

---

## Architecture

```
              ┌────────────────────────────────────────────────────────────┐
              │  Dealer / OEM / Lightspeed / eBay / Amazon                  │
              └──────────────────────────────┬─────────────────────────────┘
                                             │
                ┌────────────────────────────▼────────────────────────────┐
                │  Amazon API Gateway + Lambda authoriser                  │
                │  Routes:                                                  │
                │   POST /events/{inventory,pricing,order}   → Kinesis      │
                │   POST /runs                                → Step Functions│
                │   GET  /products?...                         → DynamoDB    │
                └─────┬──────────────────────┬─────────────────┬───────────┘
                      │                      │                 │
        ┌─────────────▼─────┐    ┌───────────▼─────┐  ┌────────▼──────────┐
        │ Kinesis Data       │    │ Step Functions  │  │ DynamoDB          │
        │ Streams            │    │ master state    │  │ (hot serving)     │
        │ (sharded by        │    │ machine reads   │  │                   │
        │  dealer_id)        │    │ dealer_bindings │  │ Partitioned by    │
        └─────────────┬──────┘    └────────┬────────┘  │ dealer_id +       │
                      │                    │           │ part_number       │
        ┌─────────────▼─────┐    ┌─────────▼─────────┐ │                   │
        │ Kinesis Data       │    │ Glue Jobs +       │ │ Sub-millisecond   │
        │ Firehose           │    │ Lambda functions  │ │ reads             │
        │  → Iceberg sink    │    │                   │ └───────────────────┘
        │  on S3             │    │ Per-pattern        │
        │                    │    │ handlers:          │
        │ Batched 5MB / 60s │    │  xlsx → bronze     │
        └─────────────┬──────┘    │  api_pull → bronze │
                      │           │  cdc → bronze      │
                      │           └─────────┬──────────┘
                      │                     │
                      └─────────┬───────────┘
                                │
              ┌─────────────────▼──────────────────────────────────────┐
              │  Amazon S3 (lakehouse)                                  │
              │                                                         │
              │  ┌────────────┐  ┌────────────┐  ┌────────────┐         │
              │  │  BRONZE    │  │  SILVER    │  │   GOLD     │         │
              │  │  Iceberg   │  │  Iceberg   │  │  Iceberg   │         │
              │  │  partition │  │  parts_    │  │  products_ │         │
              │  │  by dealer │  │  atomic    │  │  mart      │         │
              │  │            │  │  fitment_  │  │  marketplace│        │
              │  │            │  │  atomic    │  │  _view      │        │
              │  └────────────┘  └────────────┘  └─────┬──────┘         │
              │                                         │                │
              │  All tables registered in AWS Glue Data Catalog          │
              │  Queryable from Athena, Redshift Spectrum, EMR, Trino    │
              └─────────────────────────────────────────┬────────────────┘
                                                        │
                ┌───────────────┬─────────────────────┬─┴──────────┬─────────┐
                ▼               ▼                     ▼            ▼         ▼
        ┌──────────────┐ ┌──────────────┐  ┌────────────────┐ ┌────────┐ ┌────────┐
        │   Athena      │ │   Redshift    │  │ Managed Flink  │ │ Lambda │ │ Bedrock│
        │ (ad-hoc SQL)  │ │  + Spectrum   │  │ (streaming SQL │ │  sync  │ │  LLM   │
        │               │ │  (BI + analytics)│ │  on Kinesis)   │ │  jobs  │ │ calls  │
        └──────────────┘ └──────────────┘  └────────┬───────┘ └───┬────┘ └────────┘
                                                     │             │
                                                     ▼             ▼
                                          ┌──────────────────────────────┐
                                          │  Marketplace destinations    │
                                          │  via AWS AppFlow + EventBridge│
                                          │   eBay Trading API           │
                                          │   Amazon SP-API               │
                                          │   Google Shopping             │
                                          └──────────────────────────────┘

  Identity:        IAM + Cognito + per-dealer role assumption
  Observability:   CloudWatch + X-Ray traces + OpenSearch indexed logs
  Cost attribution: Tags propagated to all resources; Cost Explorer per dealer
  Infrastructure:   Terraform / AWS CDK
  DR:               S3 cross-region replication + RDS PITR + DynamoDB global tables
```

---

## What you gain

| Property                                                  | Solution A                      | Solution D (AWS)                                       |
| --------------------------------------------------------- | ------------------------------- | ------------------------------------------------------ |
| Compute scaling                                            | Single-process workers          | **Glue auto-scales Spark workers; Lambda concurrency** |
| Streaming throughput                                       | BullMQ ~few K events/sec single-host | **Kinesis: thousands of shards; MSK: linear**     |
| Analytical depth                                           | PG ad-hoc                       | **Athena + Redshift Spectrum over Iceberg, petabyte-scale**|
| Hot-read serving (catalog API)                             | PG with read replicas           | **DynamoDB single-digit ms at any scale**              |
| LLM at scale                                               | Self-managed providers          | **Bedrock managed inference + per-model billing**      |
| Multi-region                                               | Documented (Section 11)         | **S3 CRR + DynamoDB Global Tables + Route 53 failover**|
| Marketplace sync                                           | Custom workers                  | **AppFlow managed connectors + EventBridge rules**     |
| Search                                                     | PG trigram                      | **OpenSearch with relevance + multi-language analyzers**|
| Per-dealer cost attribution                                | Manual tagging                  | **Cost Explorer + per-dealer IAM tags built-in**      |
| Compliance                                                 | Manual                          | **HIPAA / SOC 2 / PCI / FedRAMP eligible services**    |

---

## What you give up

- **AWS lock-in.** Glue Catalog, Kinesis, DynamoDB, EventBridge are AWS-specific. Iceberg files in S3 remain portable; the orchestration glue does not.
- **Cost at small scale.** Glue Jobs have a 1-minute billing minimum; Step Functions charge per state transition; Kinesis charges per shard hour. A 50-dealer deployment pays for managed-service overhead.
- **Service surface area.** Twenty-plus AWS services is a lot of console familiarity, IAM policy authoring, and CloudFormation / Terraform to maintain.
- **Vendor SDK churn.** AWS SDK releases monthly; minor version bumps occasionally break Lambda runtimes or Glue job compatibility.
- **DynamoDB schema design.** Single-table design with PK/SK requires careful access-pattern modelling upfront; refactoring is harder than relational migration.

---

## When to choose Solution D

Adopt when **three or more** of these hold:

1. The organisation is already on AWS for other workloads (no platform diversification cost)
2. Ten-thousand-dealer scale is the explicit business target within 18 months
3. Multi-region or multi-tenant compliance is a contractual requirement
4. The team has at least one engineer with production AWS experience (Glue, Kinesis, Step Functions)
5. Marketplace sync to multiple destinations (eBay + Amazon + Google + Shopify) is on the roadmap
6. Annual cloud budget exceeds $50,000 (the threshold at which AWS managed services become cost-effective vs self-hosted)

If only one or two hold, Solution A is simpler and Solution B preserves OSS portability. Solution C is the choice when the platform commitment is Microsoft rather than AWS.

---

## Cost economics at scale

Indicative monthly costs for a one-thousand-dealer deployment, with representative assumptions:

| Component                                | Monthly cost (estimated)              |
| ---------------------------------------- | ------------------------------------- |
| S3 (Iceberg storage, 5 TB)               | $115                                  |
| Glue Catalog (1M objects)                | $1                                    |
| Glue Jobs (Spark, 4 DPU × 30 min/day)    | $440                                  |
| Lambda (10M invocations, 1 GB, 5 sec)    | $80                                   |
| Step Functions (1M state transitions)    | $25                                   |
| Kinesis Data Streams (10 shards)         | $110                                  |
| Kinesis Firehose (1 TB ingest)           | $30                                   |
| MSK Serverless (light usage)             | $200                                  |
| DynamoDB (on-demand, 50M reads + 5M writes) | $80                                |
| Athena (1 TB scanned)                    | $5                                    |
| Redshift Serverless (RPU 4, 8 hr/day)    | $200                                  |
| CloudWatch + X-Ray + OpenSearch (light)  | $100                                  |
| Bedrock (Claude Haiku for translations)  | $10                                   |
| AppFlow + EventBridge                     | $15                                   |
| **Total (1000 dealers)**                  | **approximately $1,400/month**       |

Per-dealer marginal cost at one thousand dealers: about $1.40 per dealer per month. At ten thousand dealers, marginal cost drops below $0.50 per dealer per month due to economies of scale on shared services.

By comparison, Solution A self-hosted runs at approximately $0.005 per dealer per month at one thousand dealers but the operational burden is real. Solution D trades cost for operational convenience.

---

## Implementation budget (AI-assisted estimate)

| Phase                                                            | Effort        |
| ---------------------------------------------------------------- | ------------- |
| AWS account baseline (Organisations, Control Tower, IAM)          | 1 day         |
| S3 lakehouse buckets + Glue Catalog + Iceberg first table        | 1 day         |
| First Glue Job: xlsx → bronze Iceberg                            | 1 day         |
| Step Functions master pipeline + DynamoDB metadata store         | 2 days        |
| Kinesis Streams + Firehose for webhook ingestion                 | 1 day         |
| DMS replication slot from RDS → Kinesis                          | 1 day         |
| Athena + Redshift Spectrum verification queries                   | 0.5 day       |
| Bedrock integration for LLM translation                          | 0.5 day       |
| API Gateway + Lambda + DynamoDB read path                        | 1 day         |
| AppFlow connectors for one marketplace                            | 1 day         |
| Terraform / CDK consolidation + CI/CD                              | 2 days        |
| Observability (CloudWatch dashboards, alarms, OpenSearch)         | 1 day         |
| Multi-region DR drill                                              | 1 day         |
| Documentation + ADR series                                         | 1 day         |

**Total: approximately 15 working days AI-assisted, or six to eight weeks manual** for a first production deployment. Each subsequent dealer onboards in approximately one hour of DynamoDB row insertion plus IAM role assignment.

---

## How Solution D relates to A, B, and C

Solution D is the **AWS-native counterpart** to Solution C's Microsoft-native architecture. Both solve the same problem at the same scale; the choice depends on the organisation's strategic cloud commitment.

Migration path from Solution A or B:

```
Solution A (PostgreSQL serving + BullMQ workers)
   │
   │  When scale triggers fire (ADR-009):
   │   • >500 dealers, OR
   │   • >50 TB historical, OR
   │   • >30% LLM cost share
   │
   ├─→ Solution B (Polars + Iceberg + Dagster)        [portable OSS]
   │       Migrate ingestion plane only; PG stays
   │
   ├─→ Solution C (Microsoft Fabric)                   [if on Microsoft 365]
   │       Full re-platform; OneLake unifies
   │
   └─→ Solution D (AWS managed services)              [if on AWS]
           Full re-platform; S3 + Iceberg + Glue Catalog unifies
```

Solutions C and D are **strategic platform decisions, not incremental scale-ups**. They exist in this submission as honest documentation of what a senior engineer would consider given different organisational baselines.
