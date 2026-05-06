# NOTPUNKS Skill NFT Marketplace

Подробная структура монетизации кастомных skills через NOT PUNKS, TNO Elemental Kids и отдельные Skill NFT.

## 1. Главная идея

NOTPUNKS Agent может стать не просто агентом с локальными skills, а полноценной экосистемой:

- холдеры создают полезные skills;
- другие холдеры устанавливают и запускают их;
- авторы получают доход;
- доступ и лицензии проверяются через NFT/TON wallet;
- вся логика остается понятной внутри CLI/TUI через `/skills`.

Ключевая продуктовая формула:

```text
NOT PUNKS matching pair = creator license
NOT PUNKS holder        = ecosystem access
TNO Elemental Kids      = Skill Pass
Skill NFT               = paid license for a specific premium skill or skill pack
```

## 1.1. Текущий MVP в агенте

Реализованный слой сейчас:

- wallet context определяет роли `not_punks_holder`, `skill_pass_holder`, `skill_creator`, `skill_license_holder`;
- matching pair NOT PUNKS + NOT PUNKS Girls с одинаковым номером открывает создание и публикацию skills;
- TNO Elemental Kids работает как `Skill Pass` для доступа к кастомным marketplace skills;
- Skill NFT license определяется по NFT metadata: `notpunks_skill_id`, `skill_id`, `skill` или `license_skill`;
- `/wallet access` показывает роли, matching pairs и найденные Skill NFT licenses;
- `/skills publish <skill> [--price TON]` создает локальный marketplace listing в `skills_marketplace/listings/`;
- `/skills access [skill]` проверяет доступ кошелька к marketplace/listing;
- `/skills buy <skill>` создает pending purchase intent до подключения TON-контрактов;
- `/skills licenses` показывает найденные Skill NFT licenses;
- `/skills earnings` показывает локальные sale records и помечает ончейн-выплаты как следующий слой.

Пока не реализовано как on-chain слой:

- mint Skill NFT;
- escrow/payment contract;
- royalty split;
- transfer/resale license;
- on-chain revocation/update policy.

Эти части должны подключаться поверх текущего локального реестра, не меняя UX команд.

## 2. NFT-роли

### 2.1. NOT PUNKS Matching Pair

**Matching pair** означает, что пользователь держит пару NOT PUNKS с одинаковыми номерами.

Пример:

```text
NOT PUNKS #123
NOT PUNKS #123
```

Такая пара дает пользователю право быть автором:

- создавать custom skills;
- публиковать skills в marketplace;
- назначать цену;
- выпускать Skill NFT;
- получать выплаты;
- обновлять свои опубликованные skills;
- выпускать paid skill packs;
- создавать creator pass для всех своих skills.

Продуктовое название:

```text
Creator License
```

Внутренний capability:

```yaml
capability: skill_creator
source: not_punks_matching_pair
```

### 2.2. NOT PUNKS Holder

Любой холдер NOT PUNKS получает общий доступ к custom skills ecosystem.

Он может:

- просматривать marketplace;
- устанавливать бесплатные holder-only skills;
- покупать premium skills;
- запускать skills, к которым у него есть доступ;
- оставлять rating/review, если такая функция будет добавлена.

Продуктовое название:

```text
Ecosystem Access
```

Внутренний capability:

```yaml
capability: skill_user
source: not_punks_holder
```

### 2.3. TNO Elemental Kids

TNO Elemental Kids не нужно переименовывать полностью. Лучше сохранить бренд коллекции и дать ей utility внутри NOTPUNKS Agent.

Продуктовая роль внутри агента:

```text
Skill Pass
```

То есть:

```text
TNO Elemental Kids = Skill Pass NFT inside NOTPUNKS Agent
```

Холдер Elemental Kids получает:

- доступ к custom skills marketplace;
- право устанавливать holder-only skills;
- право покупать premium skills;
- возможно, специальные discounts или early access;
- возможно, отдельные categories skills, доступные только Skill Pass holders.

Внутренний capability:

```yaml
capability: skill_user
source: elemental_kids_holder
label: skill_pass
```

### 2.4. Skill NFT

Skill NFT - это отдельный NFT/license, который открывает доступ к конкретному premium skill или skill pack.

Важно: Skill NFT не заменяет Elemental Kids. Это дополнительный уровень монетизации.

Пример:

```text
TON Trading Analyst Skill NFT
Supply: 100
Price: 10 TON
Utility: lifetime access to TON Trading Analyst skill
Creator: EQ...
```

Skill NFT может открывать:

- один skill;
- набор skills;
- все skills одного автора;
- временный доступ;
- lifetime access;
- pro-mode внутри бесплатного skill.

Внутренний capability:

```yaml
capability: skill_license
source: skill_nft
skill_id: ton-trading-analyst
```

## 3. Access Levels

Базовые уровни доступа:

```text
free
  доступен всем

holders
  нужен NOT PUNKS или TNO Elemental Kids

premium
  нужен NOT PUNKS или TNO Elemental Kids + покупка / Skill NFT

creator
  нужен NOT PUNKS matching pair
```

Расширенная модель:

| Access mode | Кто может использовать | Нужно платить | Для чего |
| --- | --- | --- | --- |
| `free` | любой пользователь агента | нет | onboarding, демо, open-source skills |
| `holders` | NOT PUNKS или Elemental Kids | нет | community utility |
| `premium` | holders + license | да | платные skills авторов |
| `skill_nft` | holder конкретного Skill NFT | покупка NFT | lifetime или limited access |
| `subscription` | holder active subscription token | периодически | recurring revenue |
| `creator` | matching pair holder | нет, но нужен pair | создание и публикация |

## 4. Почему нужны отдельные Skill NFT

Если оставить только NOT PUNKS и Elemental Kids:

- все holder-only skills доступны всем holder users;
- авторам трудно брать оплату за конкретный skill;
- нет механики limited licenses;
- нет secondary sales;
- нет прямого ownership конкретного продукта.

Skill NFT решают это:

- каждый paid skill получает свою лицензию;
- автор может продать 10, 100, 1000 копий;
- можно делать scarcity;
- можно делать resale royalties;
- пользователь видит в wallet, какие skills он купил;
- доступ можно проверять on-chain;
- marketplace может строить rankings, ownership, royalties.

Итоговая модель:

```text
Elemental Kids = общий Skill Pass
Skill NFT      = лицензия на конкретный premium skill
```

## 5. Типы монетизации

### 5.1. Free Skill

Skill бесплатный.

Использование:

- open-source community skill;
- demo skill;
- onboarding;
- реклама автора.

Metadata:

```yaml
access:
  mode: free
```

### 5.2. Holder-Only Skill

Skill доступен только холдерам NOT PUNKS или TNO Elemental Kids.

Metadata:

```yaml
access:
  mode: holders
  requires:
    any_collection:
      - not_punks
      - elemental_kids
```

### 5.3. Paid Install

Пользователь платит один раз, после чего получает доступ к skill.

Варианты реализации:

- off-chain license record после TON payment;
- NFT receipt;
- полноценный Skill NFT.

Metadata:

```yaml
access:
  mode: premium
  requires:
    any_collection:
      - not_punks
      - elemental_kids
  license:
    type: paid_install
    price_ton: 5
```

### 5.4. Skill NFT

Пользователь покупает NFT, который открывает skill.

Metadata:

```yaml
access:
  mode: premium
  requires:
    any_collection:
      - not_punks
      - elemental_kids
  license:
    type: skill_nft
    price_ton: 10
    supply: 100
    duration: lifetime
```

### 5.5. Skill Pack NFT

Один NFT открывает набор skills.

Пример:

```text
TON Trader Pack
- wallet-risk-analyzer
- nft-floor-scanner
- token-alert-agent
- transaction-explainer
```

Metadata:

```yaml
access:
  mode: premium
  license:
    type: skill_pack_nft
    price_ton: 25
    supply: 250
    unlocks:
      - wallet-risk-analyzer
      - nft-floor-scanner
      - token-alert-agent
      - transaction-explainer
```

### 5.6. Creator Pass

NFT открывает все premium skills конкретного автора.

Metadata:

```yaml
access:
  mode: premium
  license:
    type: creator_pass
    creator_wallet: EQ...
    price_ton: 50
    supply: 100
```

### 5.7. Subscription

Доступ на ограниченный срок.

Metadata:

```yaml
access:
  mode: premium
  license:
    type: subscription
    price_ton: 3
    period_days: 30
```

На первом этапе это можно отложить, потому что subscription сложнее, чем lifetime Skill NFT.

## 6. Рекомендуемый MVP

Для первой версии не нужно строить весь marketplace сразу.

Минимальный набор:

```text
1. creator check
2. publish skill
3. browse marketplace
4. install free/holder skills
5. buy premium skill
6. verify Skill NFT/license
7. earnings page for creator
```

MVP access modes:

```text
free
holders
premium/skill_nft
```

MVP roles:

```text
NOT PUNKS matching pair = can publish
NOT PUNKS holder        = can use
Elemental Kids holder   = can use
Skill NFT holder        = can unlock premium skill
```

## 7. Skill Metadata

### 7.1. SKILL.md Frontmatter

Каждый skill должен иметь machine-readable metadata.

Пример:

```yaml
---
name: ton-trading-analyst
title: TON Trading Analyst
description: Analyze TON wallets, NFT positions, token exposure, and trading risk.
version: 1.0.0
author:
  name: punkbuilder
  wallet: EQD...
  not_punks:
    creator_pair_number: 123
category: blockchain
tags:
  - ton
  - trading
  - wallet
  - nft
access:
  mode: premium
  requires:
    any_collection:
      - not_punks
      - elemental_kids
  license:
    type: skill_nft
    price_ton: 10
    supply: 100
    duration: lifetime
revenue:
  creator_bps: 9000
  treasury_bps: 1000
marketplace:
  status: published
  visibility: public
  icon: ipfs://...
  banner: ipfs://...
  demo_prompt: "Analyze this TON wallet: EQ..."
permissions:
  tools:
    - ton_wallet
    - nft_scan
    - web_search
  network: true
  secrets: false
---
```

### 7.2. Поля access

```yaml
access:
  mode: free | holders | premium | creator
```

`mode=free`:

```yaml
access:
  mode: free
```

`mode=holders`:

```yaml
access:
  mode: holders
  requires:
    any_collection:
      - not_punks
      - elemental_kids
```

`mode=premium`:

```yaml
access:
  mode: premium
  requires:
    any_collection:
      - not_punks
      - elemental_kids
  license:
    type: skill_nft
    price_ton: 10
    supply: 100
```

### 7.3. Поля license

```yaml
license:
  type: paid_install | skill_nft | skill_pack_nft | creator_pass | subscription
  price_ton: 10
  supply: 100
  duration: lifetime
```

Варианты `duration`:

```text
lifetime
30d
90d
365d
```

Для MVP лучше поддержать только:

```text
lifetime
```

### 7.4. Поля revenue

Рекомендуется хранить в basis points, чтобы избежать floating point.

```yaml
revenue:
  creator_bps: 9000
  treasury_bps: 1000
```

Это означает:

```text
90% creator
10% treasury
```

## 8. Marketplace Data Model

### 8.1. Skill Listing

```yaml
skill_id: ton-trading-analyst
slug: ton-trading-analyst
title: TON Trading Analyst
description: Analyze TON wallets, NFT positions, token exposure, and trading risk.
version: 1.0.0
creator:
  wallet: EQD...
  display_name: punkbuilder
  verified_pair:
    collection: not_punks
    number: 123
access:
  mode: premium
  license_type: skill_nft
price:
  amount: "10"
  currency: TON
supply:
  max: 100
  minted: 37
stats:
  installs: 431
  purchases: 37
  rating: 4.8
  reviews: 21
assets:
  icon: ipfs://...
  banner: ipfs://...
source:
  package_uri: ipfs://...
  checksum: sha256:...
status: published
created_at: "2026-05-02T00:00:00Z"
updated_at: "2026-05-02T00:00:00Z"
```

### 8.2. License Record

Если делаем off-chain cache поверх on-chain ownership:

```yaml
license_id: skill-license-ton-trading-analyst-EQ...
skill_id: ton-trading-analyst
owner_wallet: EQ...
license_type: skill_nft
nft_address: EQ...
tx_hash: ...
status: active
expires_at: null
created_at: "2026-05-02T00:00:00Z"
```

### 8.3. Creator Earnings

```yaml
creator_wallet: EQD...
total_earned_ton: "128.5"
withdrawable_ton: "73.2"
pending_ton: "4.0"
sales:
  - skill_id: ton-trading-analyst
    buyer_wallet: EQ...
    amount_ton: "10"
    creator_amount_ton: "9"
    treasury_amount_ton: "1"
    tx_hash: ...
    created_at: "2026-05-02T00:00:00Z"
```

## 9. CLI Commands

### 9.1. Creator Commands

```text
/skills create <name>
/skills publish <name>
/skills unpublish <name>
/skills price <name> <amount>
/skills license <name> [paid_install|skill_nft|skill_pack]
/skills earnings
/skills withdraw
```

### 9.2. User Commands

```text
/skills browse
/skills search <query>
/skills inspect <skill>
/skills install <skill>
/skills buy <skill>
/skills licenses
/skills owned
```

### 9.3. Admin / Debug Commands

```text
/skills verify <skill>
/skills audit <skill>
/skills access <skill>
/skills marketplace sync
/skills marketplace status
```

### 9.4. Wallet Commands

```text
/wallet connect
/wallet status
/wallet nft
/wallet access
```

`/wallet access` должен показывать:

```text
Wallet: EQ...

NOT PUNKS:
  Holder: yes
  Matching pair: #123
  Creator access: yes

TNO Elemental Kids:
  Holder: yes
  Skill Pass: yes

Skill NFTs:
  TON Trading Analyst: active
  NFT Floor Scanner: active
```

## 10. User Flows

### 10.1. Создание skill автором

```text
1. User connects wallet.
2. Agent scans NFTs.
3. Agent detects NOT PUNKS matching pair.
4. User runs /skills create ton-trading-analyst.
5. User edits SKILL.md.
6. User runs /skills publish ton-trading-analyst.
7. Agent validates metadata.
8. Agent packages skill.
9. Agent uploads package to storage/IPFS/marketplace backend.
10. Listing appears in marketplace.
```

### 10.2. Публикация premium skill

```text
1. Creator runs /skills publish.
2. CLI asks monetization model:
   - free
   - holders
   - paid install
   - skill NFT
3. Creator selects skill NFT.
4. CLI asks:
   - price
   - supply
   - royalty
   - treasury split
5. CLI generates marketplace listing.
6. Optional: smart contract/NFT collection is deployed or minted lazily.
7. Skill becomes visible as premium listing.
```

### 10.3. Покупка premium skill пользователем

```text
1. User runs /skills browse.
2. User selects premium skill.
3. CLI checks wallet.
4. CLI checks NOT PUNKS or Elemental Kids.
5. If no ecosystem access: deny with explanation.
6. If ecosystem access exists but no Skill NFT: show price.
7. User confirms purchase.
8. TON payment is sent.
9. Skill NFT/license is minted or assigned.
10. Agent verifies license.
11. Skill installs locally.
```

### 10.4. Запуск premium skill

```text
1. User invokes skill.
2. Agent checks local access cache.
3. If cache is fresh, allow.
4. If cache is stale, verify wallet/NFT ownership.
5. If license active, run skill.
6. If license missing, show buy/install prompt.
```

## 11. Access Check Logic

### 11.1. Creator Check

Pseudo-code:

```python
def can_publish_skill(wallet):
    nfts = scan_wallet_nfts(wallet)
    return has_not_punks_matching_pair(nfts)
```

### 11.2. Ecosystem User Check

```python
def has_skill_marketplace_access(wallet):
    nfts = scan_wallet_nfts(wallet)
    return has_not_punks(nfts) or has_elemental_kids(nfts)
```

### 11.3. Premium Skill Check

```python
def can_use_premium_skill(wallet, skill_id):
    if not has_skill_marketplace_access(wallet):
        return False
    if has_skill_nft(wallet, skill_id):
        return True
    if has_paid_install_license(wallet, skill_id):
        return True
    return False
```

### 11.4. Full Access Decision

```python
def can_install_skill(wallet, skill):
    if skill.access.mode == "free":
        return True

    if skill.access.mode == "holders":
        return has_skill_marketplace_access(wallet)

    if skill.access.mode == "premium":
        return can_use_premium_skill(wallet, skill.id)

    if skill.access.mode == "creator":
        return can_publish_skill(wallet)

    return False
```

## 12. Smart Contract / TON Architecture

### 12.1. MVP Without Complex Contracts

Для MVP можно не писать сложный contract сразу.

Вариант:

```text
User pays TON -> treasury/escrow wallet
Backend/indexer sees tx
Backend records license
Optional NFT minted later
```

Плюсы:

- быстрее запустить;
- меньше contract risk;
- легче менять механику.

Минусы:

- license более off-chain;
- ниже trustlessness.

### 12.2. Skill NFT Contract

Более правильная версия:

```text
Skill NFT collection per skill
or
Single Skill License collection with skill_id in metadata
```

Вариант A: отдельная collection на каждый skill.

```text
TON Trading Analyst Skill NFT Collection
NFT #1
NFT #2
...
```

Плюсы:

- понятно коллекционерам;
- легко показывать supply;
- легко торговать отдельно.

Минусы:

- много collections;
- сложнее индексировать.

Вариант B: одна общая collection.

```text
NOTPUNKS Skill Licenses
NFT #1 -> skill_id=ton-trading-analyst
NFT #2 -> skill_id=nft-floor-scanner
```

Плюсы:

- проще инфраструктура;
- один indexer;
- один contract стандарт.

Минусы:

- меньше индивидуального брендинга каждого skill.

Рекомендация:

```text
MVP: одна общая NOTPUNKS Skill Licenses collection
Later: premium creators can deploy branded skill collections
```

### 12.3. Royalty

Skill NFT metadata должна поддерживать royalty:

```yaml
royalty:
  creator_bps: 500
  recipient: EQ...
```

Это 5% на secondary sales.

### 12.4. Revenue Split

На primary sale:

```text
90% creator
10% treasury
```

Для premium platform services можно добавить:

```text
85% creator
10% treasury
5% referrer/curator
```

Но MVP лучше:

```text
creator_bps: 9000
treasury_bps: 1000
```

## 13. Marketplace Storage

### 13.1. Skill Package

Skill package должен быть reproducible archive.

Пример структуры:

```text
ton-trading-analyst.skill.tar.gz
  SKILL.md
  scripts/
  references/
  assets/
  manifest.json
```

`manifest.json`:

```json
{
  "skill_id": "ton-trading-analyst",
  "version": "1.0.0",
  "checksum": "sha256:...",
  "entry": "SKILL.md",
  "created_at": "2026-05-02T00:00:00Z"
}
```

### 13.2. Storage Options

MVP:

```text
central marketplace backend + checksum
```

Later:

```text
IPFS / TON Storage / Arweave mirror
```

Recommended staged approach:

```text
Phase 1: backend storage
Phase 2: backend + IPFS mirror
Phase 3: decentralized package registry
```

## 14. Security Model

Skills can be dangerous because they may contain scripts and tool instructions.

Marketplace must enforce:

- metadata validation;
- package checksum;
- max package size;
- path traversal protection;
- allowed file extensions;
- tool permission declaration;
- no hidden binary execution unless approved;
- no secrets exfiltration;
- audit status;
- version immutability.

### 14.1. Permission Metadata

```yaml
permissions:
  tools:
    - terminal
    - web_search
    - ton_wallet
  network: true
  filesystem:
    read: true
    write: false
  secrets: false
```

### 14.2. Audit Status

```yaml
audit:
  status: pending | passed | rejected | warning
  reviewer: system | admin | community
  notes:
    - "Uses network access"
    - "No secret access requested"
```

Marketplace UI should show:

```text
Audit: passed
Permissions: network, ton_wallet
Risk: medium
```

### 14.3. Version Immutability

Published versions should be immutable.

If creator updates skill:

```text
1.0.0 remains available
1.1.0 is published as new version
```

This protects users from silent malicious updates.

## 15. Local Cache

Agent should maintain local access cache:

```yaml
wallet: EQ...
updated_at: "2026-05-02T00:00:00Z"
collections:
  not_punks:
    holder: true
    matching_pairs:
      - 123
  elemental_kids:
    holder: true
licenses:
  ton-trading-analyst:
    active: true
    source: skill_nft
    nft_address: EQ...
```

Cache TTL:

```text
5 minutes for active CLI session
1 hour for marketplace browse
always refresh before paid install/run
```

## 16. UX Copy

### 16.1. No Wallet

```text
Connect your TON wallet to install marketplace skills.
Подключите TON кошелек, чтобы устанавливать marketplace skills.
```

### 16.2. No Ecosystem Access

```text
This skill requires NOT PUNKS or TNO Elemental Kids.
Этот skill требует NOT PUNKS или TNO Elemental Kids.
```

### 16.3. No Creator License

```text
Publishing skills requires a matching NOT PUNKS pair.
Для публикации skills нужна пара NOT PUNKS с одинаковым номером.
```

### 16.4. Premium Skill Locked

```text
This premium skill requires a Skill NFT license.
Для этого premium skill нужна Skill NFT лицензия.
```

### 16.5. Purchase Success

```text
Skill NFT minted. Skill unlocked.
Skill NFT выпущен. Skill разблокирован.
```

## 17. UI / TUI Marketplace

Marketplace screen should show:

```text
┌──────────────────────────────────────────────┐
│ TON Trading Analyst                          │
│ Analyze TON wallet risk and NFT exposure.    │
│                                              │
│ Creator: punkbuilder                         │
│ Access: Premium / Skill NFT                  │
│ Price: 10 TON                                │
│ Supply: 37 / 100 minted                      │
│ Rating: 4.8                                  │
│                                              │
│ [Inspect] [Buy] [Install]                    │
└──────────────────────────────────────────────┘
```

Filters:

```text
All
Free
Holder-only
Premium
Owned
Installed
By creator
```

Sort:

```text
Trending
Newest
Most installed
Top rated
Price low-high
Price high-low
```

## 18. Backend API Shape

### 18.1. Browse

```http
GET /api/skills
```

Response:

```json
{
  "skills": [
    {
      "skill_id": "ton-trading-analyst",
      "title": "TON Trading Analyst",
      "access_mode": "premium",
      "price_ton": "10",
      "creator_wallet": "EQ...",
      "rating": 4.8
    }
  ]
}
```

### 18.2. Inspect

```http
GET /api/skills/{skill_id}
```

### 18.3. Publish

```http
POST /api/skills/publish
```

Requires:

```text
wallet signature
matching pair proof
skill package
metadata
```

### 18.4. Purchase

```http
POST /api/skills/{skill_id}/purchase
```

Returns:

```json
{
  "payment_address": "EQ...",
  "amount_ton": "10",
  "payload": "skill_purchase:ton-trading-analyst:EQ..."
}
```

### 18.5. Verify Access

```http
POST /api/skills/{skill_id}/verify-access
```

Request:

```json
{
  "wallet": "EQ..."
}
```

Response:

```json
{
  "allowed": true,
  "reason": "skill_nft",
  "license": {
    "type": "skill_nft",
    "nft_address": "EQ..."
  }
}
```

## 19. Local Command Implementation Plan

### 19.1. Phase 1: Metadata and Access Checks

Implement:

```text
Skill access metadata parsing
Wallet NFT scan integration
Matching pair detection
Elemental Kids holder detection
Skill NFT detection placeholder
```

Commands:

```text
/wallet access
/skills access <skill>
```

### 19.2. Phase 2: Publish Flow

Implement:

```text
/skills publish
metadata wizard
creator rights check
package generation
local marketplace registry
```

### 19.3. Phase 3: Marketplace Browse/Install

Implement:

```text
/skills browse
/skills inspect
/skills install
```

Access:

```text
free
holders
premium locked message
```

### 19.4. Phase 4: Purchase + Skill NFT

Implement:

```text
/skills buy
TON payment request
payment confirmation
license mint/record
install after purchase
```

### 19.5. Phase 5: Earnings

Implement:

```text
/skills earnings
/skills withdraw
creator sales report
```

## 20. Recommended First Version Rules

Use strict but simple rules:

```text
Only matching NOT PUNKS pair holders can publish.
Only NOT PUNKS or Elemental Kids holders can install custom marketplace skills.
Premium skills require Skill NFT.
Skill NFT is lifetime in v1.
Revenue split is 90/10.
Published versions are immutable.
All package installs verify checksum.
```

## 21. Example End-to-End Scenario

Creator:

```text
1. Holds NOT PUNKS #123 + #123.
2. Runs /wallet connect.
3. Runs /skills create ton-trading-analyst.
4. Builds skill.
5. Runs /skills publish ton-trading-analyst.
6. Chooses "premium / skill NFT".
7. Sets price 10 TON, supply 100.
8. Marketplace publishes listing.
```

User:

```text
1. Holds TNO Elemental Kid.
2. Runs /wallet connect.
3. Runs /skills browse.
4. Finds TON Trading Analyst.
5. Runs /skills buy ton-trading-analyst.
6. Pays 10 TON.
7. Receives Skill NFT.
8. Runs /skills install ton-trading-analyst.
9. Uses skill.
```

Revenue:

```text
Sale: 10 TON
Creator: 9 TON
Treasury: 1 TON
```

## 22. Open Questions

Need product decisions:

1. Should NOT PUNKS holders and Elemental Kids holders have identical user access, or should Elemental Kids get extra marketplace discounts?
2. Should Skill NFT be minted immediately on purchase, or should v1 use off-chain license records first?
3. Should each skill have its own NFT collection, or should there be one shared `NOTPUNKS Skill Licenses` collection?
4. Should creators be allowed to publish unlimited skills per matching pair?
5. Should there be creator reputation / audit badges?
6. Should paid skills be allowed to request terminal/filesystem permissions?
7. Should treasury split be fixed or creator-configurable?

Recommended answers for v1:

```text
1. Same access for NOT PUNKS and Elemental Kids.
2. Use Skill NFT if feasible; otherwise off-chain license as temporary bridge.
3. One shared NOTPUNKS Skill Licenses collection.
4. Unlimited skills, but rate-limit publishing.
5. Add audit badges after MVP.
6. Allow with visible permission warnings.
7. Fixed 90/10 split.
```

## 23. Final Product Positioning

Short version:

```text
NOTPUNKS Agent lets NFT holders create, sell, and use AI skills.
Matching NOT PUNKS pairs unlock creator rights.
NOT PUNKS and TNO Elemental Kids unlock marketplace access.
Skill NFTs unlock premium skills and let creators earn TON.
```

User-facing version:

```text
Create skills. Token-gate them. Sell access. Let your agent earn with you.
```

Russian version:

```text
Создавайте skills. Открывайте доступ через NFT. Продавайте premium skills. Получайте TON за свои AI-автоматизации.
```
