## SaaS Subscription Analytics Data Warehouse

A sample star-schema data warehouse for practicing SaaS subscription analytics using SQL.

The dataset models a B2B SaaS company that sells seat-based subscriptions across three plans:

Starter
Growth
Enterprise

Customers move through a typical SaaS lifecycle:

Trial → Conversion → Active → Upgrade/Downgrade → Payment Failure → Recovery/Churn → Reactivation

This project is designed to help practice real-world SaaS analytics concepts such as:

- MRR (Monthly Recurring Revenue)
- ARR (Annual Recurring Revenue)
- Churn Analysis
- Retention & Cohorts
- Net Revenue Retention (NRR)
- Subscription Lifecycle Analysis
- Payment Recovery (Dunning)
- SCD Type 2 Customer Dimensions
- As-of Date Reporting

#### **Data Model**

The warehouse follows a star schema design with dimension and fact tables.

**Dimensions:**
| Table           | Description                            |
| --------------- | -------------------------------------- |
| `dim_date`      | Calendar and reporting dates           |
| `dim_plan`      | Subscription plans and billing periods |
| `dim_sales_rep` | Sales representative details           |
| `dim_customer`  | Customer dimension (SCD Type 2)        |

**Facts:**
| Table                              | Description                                 |
| ---------------------------------- | ------------------------------------------- |
| `fact_subscription_events`         | Subscription lifecycle events               |
| `fact_daily_subscription_snapshot` | Daily subscription status and MRR snapshots |
| `fact_invoice_payments`            | Invoice and payment activity                |
| `fact_product_usage`               | Daily customer product usage                |

**Dataset Size:**
| Table                              |  Rows |
| ---------------------------------- | ----: |
| `dim_date`                         |   273 |
| `dim_plan`                         |     6 |
| `dim_sales_rep`                    |     7 |
| `dim_customer`                     |    35 |
| `fact_subscription_events`         |   101 |
| `fact_daily_subscription_snapshot` | 4,252 |
| `fact_invoice_payments`            |   145 |
| `fact_product_usage`               | 4,362 |


**Build the database from scratch:**
- python 01_build_schema_and_dims.py
- python 02_seed_facts.py

**Key Learning Areas:**
- Star Schema Modeling
- SaaS Subscription Metrics
- Customer Lifecycle Analytics
- SCD Type 2 Dimensions
- Snapshot vs Event-Based Fact Tables
- Revenue Reporting
- Cohort & Retention Analysis
- Advanced SQL Queries Practice

**Notes:**
Some customers change business segments over time (e.g., Mid-Market → Enterprise). The customer dimension uses SCD Type 2 to preserve historical changes and support accurate "as-of" reporting.

The dataset also includes realistic payment-failure and recovery scenarios to support churn and dunning analysis.