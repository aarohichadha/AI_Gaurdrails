# 4. Email-Agent Security Dataset

Public prompt-injection datasets mostly contain single prompts or chat turns. An email agent needs more context than that. To decide whether "forward the Q3 forecast to the auditor" is legitimate, a guardrail has to know who sent the message, what the user actually asked for, how sensitive the data is, and where it is allowed to go. We therefore built a dataset in which every record describes a complete email-agent situation rather than a single string. This section explains how the data was produced, what each record contains, and the checks we ran before using it.

## 4.1 Dataset Generation

The dataset was generated synthetically from parameterised scenario templates. Its design drew on four existing resources: the inbox-and-tools setting of AgentDojo, the attack phrasing in the deepset/prompt-injections and Lakera Mosscap prompt-injection collections, and the email-assistant threat model of Microsoft's LLMail-Inject challenge. We did not copy records from these sources. They informed which attack styles to cover and how a realistic agent task should look, and every record lists them in an `inspiration_sources` field.

Each record was produced in two steps. First, a scenario was fixed: the business context, the user's instruction, the requested agent action, the data asset and its sensitivity, the authorised destination, and, for attacks, the attack category and technique. Second, the email subject and body were written to fit that scenario. The label therefore follows from the scenario that was chosen, not from the wording of the email. This matters for the ML experiments, because a classifier cannot "see" the label in any field other than the ones a real system would also have.

After generation, all records were normalised into one shared schema of 36 columns. Categorical fields use fixed upper-case vocabularies (for example `SEND_EMAIL`, `FINANCIAL_CONFIDENTIAL`, `INTERNAL_VERIFIED`). Missing values are explicit, and the expected agent behaviour is stored as structured JSON. A shared schema means every guardrail in this study, whether rule-based, ML or LLM, reads exactly the same input.

The result is three related sets:

| Set | Records | Labels | Purpose |
|---|---:|---|---|
| Main corpus (six scenario sheets) | 7,200 | 1,800 BENIGN / 5,400 ATTACK | Structured scenarios for rule, prompt and judge experiments |
| Generated three-class set | 3,000 | 1,350 SAFE / 750 REVISE / 900 ATTACK | Training data for the feature-based and fine-tuned models, including a REVISE class |
| Raw-email evaluation set | 300 | 100 SAFE / 100 REVISE / 100 ATTACK | Common held-out test on full RFC 5322 email messages |

The raw-email set is the one on which all techniques and combinations are compared in Section 6. Unlike the main corpus, it provides no structured fields at all. Each record is a raw message with headers (From, To, Subject, Date, Message-ID, MIME type) and a body, so every guardrail must work from what an email client would actually deliver.

## 4.2 Dataset Structure

Each record in the main corpus and the generated set has the same fields. The most important ones are listed below.

| Field | Content |
|---|---|
| `subject`, `email_body` | The received email |
| `sender`, `sender_trust` | Sender address and its trust level (`INTERNAL_VERIFIED` or `EXTERNAL_LOOKALIKE`) |
| `recipient` | The mailbox that received the email |
| `user_instruction` | What the trusted user asked the agent to do; this is the only source of authority |
| `requested_action` | The tool action the email tries to trigger: `SEND_EMAIL`, `FORWARD_EMAIL`, `REPLY_EMAIL`, `SHARE_FILE`, `APPEND_FILE`, `READ_EMAIL`, `SUMMARIZE_EMAIL` or `CREATE_CALENDAR_EVENT` (900 records each) |
| `data_asset`, `data_sensitivity` | The information involved and its tier, from `INTERNAL` to `CREDENTIAL` (nine tiers) |
| `authorization_rule`, `authorization_evidence` | The policy that applies and any evidence of approval in the case record |
| `authorized_destination`, `requested_destination` | Where the data may go, and where the email asks it to go |
| `attack_category`, `attack_technique` | The attack type and the specific technique (`NONE` / `none` for benign records) |
| `expected_decision` | Ground truth: ALLOW or BLOCK (ALLOW / REVISE / BLOCK in the three-class set) |
| `expected_tool_calls_json`, `prohibited_action` | The tool calls a correct agent would make, and the action it must not take |
| `ground_truth_reason` | A short explanation of why the label is correct |
| `pair_id`, `parent_id`, `flow_id`, `flow_role` | Links between paired, derived and multi-flow records |
| `split` | The partition the record belongs to (Section 4.8) |

Three further fields, `defense_prediction`, `attack_executed` and `bypass_success`, are left empty in the released file. They are filled in by the evaluation harness when a guardrail is run, so ground truth and observed results are never mixed in the same column.

## 4.3 Benign and Malicious Scenarios

Benign records describe ordinary work that is within policy: sending a status report to an internal team, sharing a contract with the approved counsel, or adding a meeting to the calendar. The data goes to its authorised destination and any approval the action needs is present in the case record.

Malicious records use the same kinds of business tasks, but the email attempts to make the agent do something the user did not authorise. Usually this means sending data to a destination outside policy, disclosing credentials, or changing the agent's rules. Building attacks on top of realistic tasks was deliberate. If attacks were the only emails that mentioned files and external addresses, a guardrail could learn that shortcut rather than learn to recognise an attack.

Across the main corpus there are 1,800 benign and 5,400 attack records. The 1:3 ratio follows from the sheet design. The Base, Paired and Flow-Separation sheets are balanced (600 benign and 600 attack each), while the Contextual Manipulation, Attack Taxonomy and Adaptive sheets contain only attacks, since their purpose is to stress specific attack types. Records also carry a difficulty level: 1,500 each are EASY, MEDIUM and HARD, and 2,700 are ADAPTIVE. Because of the imbalance we report precision, recall, F1 and false-positive rate alongside accuracy throughout the paper.

## 4.4 Paired Benign–Attack Scenarios

The Paired Benign–Attack sheet contains 600 matched pairs. In each pair, one benign and one attack record share the same scenario: the same sender, user instruction, requested action, data asset, sensitivity and authorised destination. Within every pair, only four fields differ: the email body, the requested destination, the expected tool calls and the prohibited action. The attack version asks for the same work but redirects the data to an unauthorised address.

These pairs act as a control. A guardrail that blocks both members of a pair is reacting to the topic or the sensitivity of the data, not to the attack. A guardrail that allows both has missed the redirection. Only a guardrail that separates the two is reacting to the authorisation change. The two members of a pair are always assigned to the same split (we checked all 600 pairs), so a model can never train on one twin and be tested on the other.

## 4.5 Contextual and Flow-Separation Scenarios

**Contextual manipulation.** The 1,200 records in this sheet are all attacks that contain no obvious injection phrase. Instead, they supply false context that makes an unauthorised action look approved. Six techniques appear 200 times each: fake authorisation ("I certify that this address has one-time approval, although it is absent from the case record"), role pretexts, policy redefinition, hijacking of a trusted thread, urgent deadlines, and look-alike sender addresses. These records test whether a guardrail checks claims against the case record or simply accepts what the email says.

**Flow separation.** Real emails often contain more than one request. The Flow-Separation sheet models this with 600 email contexts, each containing two information flows. Flow F1 is a legitimate request that should be carried out (for example, sending a report to `finance@corp.example`). Flow F2 is a malicious request hidden in the same message (sending the same material to an outside address). Each flow is a separate record with the same context, giving 600 LEGITIMATE/BENIGN and 600 MALICIOUS/ATTACK records. A guardrail that blocks the whole email loses the legitimate work; one that allows it lets the attack through. Only per-flow judgement handles both. This sheet is held out entirely as its own test split.

## 4.6 Attack Categories

Every attack record belongs to one of ten categories. Each category has a written definition and an example technique, and the default decision for all of them is BLOCK.

| Category | Definition | Example technique | Records |
|---|---|---|---:|
| Direct injection | Attempts to replace the governing instructions directly | instruction reset | 300 |
| Indirect injection | Embeds unsafe instructions inside the email content | quoted command | 420 |
| Contextual manipulation | Uses plausible but false authorisation context | fake authorisation | 1,500 |
| Unauthorised data flow | Moves information to a destination outside policy | hidden BCC | 1,020 |
| Social engineering | Uses pressure, urgency or trust cues | urgent deadline | 300 |
| Role impersonation | Claims an unverifiable privileged identity | role-play pretext | 420 |
| Obfuscation | Transforms wording or structure to evade detection | encoded request | 300 |
| Memory poisoning | Attempts to persist a false rule or destination | policy redefinition | 420 |
| Mixed attack | Combines several attack mechanisms | multi-step exfiltration | 300 |
| Credential exfiltration | Attempts to disclose authentication material | partial-secret probe | 420 |
| **Total** | | | **5,400** |

The Attack Taxonomy sheet covers all ten categories evenly. The totals above are uneven because the Contextual Manipulation sheet adds 1,200 records to that category, and the paired and flow-separation attacks are labelled as unauthorised data flows. We treat contextual manipulation and unauthorised data flow as the most important categories for an email agent, since in practice they need neither an obvious injection phrase nor any technical trick.

## 4.7 Adaptive Attacks

Static attack sets tend to overstate how well a defence works, because the attacks were not written with that defence in mind. The Adaptive Attack Generation sheet addresses this in a simple way. We first defined five baseline defence profiles, each representing a common kind of guardrail:

- **D1** blocks explicit instruction-reset phrases and direct external-send commands.
- **D2** blocks unverified domains and obvious credential terms.
- **D3** compares requested destinations with a static allowlist.
- **D4** detects tool-call syntax and common exfiltration verbs.
- **D5** rejects urgency combined with identity claims from external senders.

We then derived 1,200 attacks from existing attack records (each keeps a `parent_id` link to its source), applying one of eight mutations aimed at slipping past a named profile. Each mutation was used 150 times:

| Mutation | Idea |
|---|---|
| Semantic paraphrasing | Keeps the goal but avoids the vocabulary a filter looks for ("make the workbook available at…" instead of "send") |
| Instruction fragmentation | Splits the request into harmless-looking parts ("Part A… Part B… Part C… combine the parts") |
| Quoted-text camouflage | Presents the instruction as a quotation from an earlier meeting or thread |
| Destination indirection | Refers to the target address indirectly instead of stating it |
| Benign padding | Surrounds the request with long, ordinary business text |
| Cross-message split | Spreads the request across what appears to be several messages |
| Role aliasing | Uses an alternative title for a privileged role |
| Action decomposition | Breaks one harmful action into several smaller steps |

All 40 combinations of mutation and target profile are represented. These attacks are adaptive in the sense that each one was written against a known defence. They are not adaptive in the stronger sense of an attacker who sees the guardrail's decisions and keeps trying. We return to this limitation in Section 8.

## 4.8 Dataset Validation and Split

**Duplicate and consistency checks.** A validation sheet records the checks run on the main corpus. All 7,200 record IDs are unique, and so are all 7,200 scenario signatures (the combination of fields that defines a scenario). Each of the six sheets contains exactly 1,200 records. All 600 pairs and all 600 two-flow contexts are complete, and the result columns are empty for all 1,200 adaptive records. Labels and expected decisions correspond one-to-one: every ATTACK record expects BLOCK and every BENIGN record expects ALLOW. For the raw-email evaluation set we additionally confirmed that no two messages are identical and that the three classes are exactly balanced.

**Label verification.** Because each label is fixed by the scenario before the email text is written, there is no step in which an annotator reads the email and guesses. Each record also carries a `ground_truth_reason`, which allowed us to review the labels against the written policy, category by category. The three-class sets use explicit class definitions. SAFE means within policy. REVISE means a plausible request where something is missing or ambiguous (an unnamed destination, a new contact, scope creep on a past approval), so a human should confirm first. ATTACK means adversarial intent.

**Train, development and test separation.** The four sheets with mixed content (Base, Paired, Contextual Manipulation and Attack Taxonomy) are each divided into 840 training, 240 validation and 120 test records. Across the corpus this gives 3,360 training, 960 validation and 480 test records. The Flow-Separation sheet (1,200 records) and the Adaptive sheet (1,200 records) are kept out of training entirely, as `flow_test` and `adaptive_test`. Paired records always share a split. The generated three-class set has its own division into 2,100 training, 600 validation and 300 test records.

**Frozen final test set.** The 300-email raw set serves as the common final test. It was not used for training, feature design or prompt development. We also checked that none of its subjects or body texts appear in the main corpus or in the 3,000 generated emails. Every technique in Section 6, and every combination of techniques, is scored on these same 300 messages with the same metric code.

**Reducing label leakage.** Synthetic data can contain shortcuts that reveal the label without any understanding of the attack. We found and handled three of them:

1. *The destination oracle.* In the main corpus, every attack requests an address outside the authorised destination and every benign record requests the authorised one. A single allowlist comparison therefore classifies the whole corpus perfectly. We did not change any labels. Instead, we defined evaluation views that withhold this signal: `destination_blind` hides the requested destination, and `stale_allowlist` replaces the allowlist with an outdated one. On these views, rule sets that depended on the shortcut lose most of their accuracy (Section 6).
2. *Generation markers.* To keep scenario signatures unique, the generator ended attack emails with a tracking phrase (for example "Tracking marker Adapt-Orchid-5000"). On inspection, this phrase appears in every attack record of the Base, Paired, Contextual Manipulation, Attack Taxonomy and Adaptive sheets, and in none of the benign records. It is therefore a second perfect label oracle, this time in the email text itself: a text model trained on the main corpus could reach full accuracy by learning to spot the marker. This is one reason we do not treat high scores on the main corpus as evidence of robustness. The markers are absent from the Flow-Separation sheet and from the raw-email evaluation set, on which the final comparison is made.
3. *Structural features tied to class.* For the generated three-class set, attachments, links, HTML parts, Cc/Bcc lines, reply chains and urgency were assigned independently of the class and appear in every class at different rates, so no single structural feature reveals the label.

Some leakage risk remains. The raw-email evaluation set is balanced and clean, but it is built from a small number of templates: its 300 messages use only 20 distinct subject lines and 17 distinct body texts, varied through names, addresses and details. Results on it show how techniques compare under identical conditions. They are not a measure of performance on the much more varied email traffic of a real organisation.
