"""
Hardcoded demo corpus and pre-computed artifacts.
Real services replace these fixtures outside demo mode.
"""

from datetime import datetime, timezone
from models.document import Document
from models.narrative import NarrativeCluster, MutationEntry
from models.report import InvestigationReport, EvidenceItem
from models.graph import NarrativeGraph, GraphNode, GraphEdge


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# CORPUS — Hidden Energy Tax (20 documents)
# Phrase evolution:
#   docs 1–6  : "hidden energy tax"
#   docs 7–12 : "secret energy surcharge"
#   docs 13–17: "backdoor tax on power bills"
#   docs 18–20: "ratepayer burden"
# ---------------------------------------------------------------------------

DEMO_DOCUMENTS: list[Document] = [
    # --- Forum posts (2026-06-01) ---
    Document(
        id="doc_001",
        source_name="r/EnergyConsumers",
        source_type="forum",
        url="https://reddit.com/r/energyconsumers/comments/abc001",
        title="Anyone else notice the hidden energy tax buried in your bill?",
        published_at=_dt("2026-06-01T08:14:00"),
        text=(
            "I just got my utility bill and noticed a new line item nobody explained. "
            "Looks like there is a hidden energy tax that the utility is not publicizing. "
            "My bill went up $28 this month compared to the same period last year. "
            "Has anyone else seen this?"
        ),
        entities=["utility bill", "energy tax"],
        phrases=["hidden energy tax", "utility bill increase", "unexplained line item"],
    ),
    Document(
        id="doc_002",
        source_name="EnergyWatchdog Forums",
        source_type="forum",
        url="https://energywatchdog.net/forum/t/hidden-energy-tax-2026",
        title="The hidden energy tax nobody is talking about",
        published_at=_dt("2026-06-01T11:45:00"),
        text=(
            "Several members have flagged a pattern in their utility statements. "
            "A hidden energy tax seems to be rolling out across multiple providers simultaneously. "
            "This does not appear in any official rate schedule we can find. "
            "We are collecting screenshots — please share yours."
        ),
        entities=["utility providers", "rate schedule"],
        phrases=["hidden energy tax", "rate schedule", "utility statement"],
    ),
    Document(
        id="doc_003",
        source_name="ClimateSkepticBoard",
        source_type="forum",
        url="https://climateskepticboard.com/posts/hidden-energy-tax-rollout",
        title="Government-coordinated hidden energy tax already hitting ratepayers",
        published_at=_dt("2026-06-01T17:22:00"),
        text=(
            "It looks like a coordinated rollout of a hidden energy tax across three states. "
            "Officials have not acknowledged it in any press briefing. "
            "This is the kind of thing they bury in the fine print so nobody notices. "
            "Share this before it gets scrubbed."
        ),
        entities=["government", "ratepayers", "three states"],
        phrases=["hidden energy tax", "coordinated rollout", "fine print"],
    ),
    # --- Blog posts (2026-06-02) ---
    Document(
        id="doc_004",
        source_name="TheIndependentVoice.blog",
        source_type="blog",
        url="https://theindependentvoice.blog/2026/06/02/hidden-energy-tax-explained",
        title="What is the hidden energy tax and why is your utility not telling you?",
        published_at=_dt("2026-06-02T09:00:00"),
        text=(
            "Multiple forum reports are pointing to a hidden energy tax buried inside utility bills. "
            "The charge appears under ambiguous labels such as 'Infrastructure Recovery Charge' or 'Grid Modernization Fee.' "
            "Utilities are not required to publicize the purpose of these line items in most states. "
            "We compiled reader submissions from eleven states."
        ),
        entities=["Infrastructure Recovery Charge", "Grid Modernization Fee", "utilities"],
        phrases=["hidden energy tax", "infrastructure recovery charge", "grid modernization fee"],
    ),
    Document(
        id="doc_005",
        source_name="RatepayerRights.org",
        source_type="blog",
        url="https://ratepayerrights.org/blog/2026/06/hidden-energy-tax-tracker",
        title="Tracking the hidden energy tax: a state-by-state breakdown",
        published_at=_dt("2026-06-02T13:30:00"),
        text=(
            "Our tracker now covers reports from fourteen states pointing to what consumers are calling a hidden energy tax. "
            "The charge ranges from $9 to $41 per month depending on provider. "
            "Ratepayer advocacy groups have filed information requests in six states. "
            "No utility has formally confirmed the uniform nature of the charge."
        ),
        entities=["ratepayer advocacy groups", "utilities", "fourteen states"],
        phrases=["hidden energy tax", "ratepayer advocacy", "information request"],
    ),
    Document(
        id="doc_006",
        source_name="ConsumerPowerBlog",
        source_type="blog",
        url="https://consumerpowerblog.net/2026/06/02/is-the-hidden-energy-tax-real",
        title="Is the hidden energy tax real? We checked the filings.",
        published_at=_dt("2026-06-02T16:00:00"),
        text=(
            "We reviewed publicly available rate filings from eight utilities and found no single line item called a hidden energy tax. "
            "However, aggregate infrastructure surcharges have increased by an average of 18 percent since January. "
            "Whether this constitutes a hidden energy tax depends on how you define transparency. "
            "Regulators have not commented."
        ),
        entities=["rate filings", "utilities", "regulators"],
        phrases=["hidden energy tax", "infrastructure surcharge", "rate filing"],
    ),
    # --- Local news (2026-06-03) ---
    Document(
        id="doc_007",
        source_name="Springfield Gazette",
        source_type="local_news",
        url="https://springfieldgazette.com/news/2026/06/03/secret-energy-surcharge-investigation",
        title="Residents report secret energy surcharge on monthly bills",
        published_at=_dt("2026-06-03T07:30:00"),
        text=(
            "Springfield residents are raising concerns about a secret energy surcharge appearing on utility bills this month. "
            "The charge, listed under 'System Reliability Fee,' has not been explained by the local provider. "
            "Commissioner Paula Reyes said her office is reviewing the matter. "
            "Three households contacted the Gazette with bills showing the same line item."
        ),
        entities=["Springfield", "Paula Reyes", "System Reliability Fee"],
        phrases=["secret energy surcharge", "system reliability fee", "utility bill"],
    ),
    Document(
        id="doc_008",
        source_name="Riverside County Herald",
        source_type="local_news",
        url="https://riversidecountyherald.com/2026/06/03/secret-energy-surcharge",
        title="What is the secret energy surcharge? Local utility stays silent.",
        published_at=_dt("2026-06-03T10:00:00"),
        text=(
            "Riverside County customers of Western Grid Energy are questioning a secret energy surcharge added without notice. "
            "The utility declined to comment for this story. "
            "State regulators confirmed they received three formal complaints in May. "
            "Energy attorney Marcus Bell called the lack of disclosure 'a regulatory gray area.'"
        ),
        entities=["Western Grid Energy", "Marcus Bell", "Riverside County"],
        phrases=["secret energy surcharge", "Western Grid Energy", "regulatory disclosure"],
    ),
    Document(
        id="doc_009",
        source_name="Millbrook Daily",
        source_type="local_news",
        url="https://millbrookdaily.com/local/2026/06/03/energy-surcharge-complaints",
        title="Energy surcharge complaints rise across Millbrook district",
        published_at=_dt("2026-06-03T12:15:00"),
        text=(
            "The Millbrook Public Utilities Commission received 47 complaints this week related to a secret energy surcharge. "
            "Officials say the surcharge was authorized under a 2024 grid improvement ordinance but was never communicated to customers. "
            "Consumer advocates say the lack of notice violates state disclosure rules. "
            "A public hearing is scheduled for June 15."
        ),
        entities=["Millbrook Public Utilities Commission", "grid improvement ordinance", "consumer advocates"],
        phrases=["secret energy surcharge", "public utilities commission", "grid improvement ordinance"],
    ),
    Document(
        id="doc_010",
        source_name="Twin Oaks Observer",
        source_type="local_news",
        url="https://twinoaksobserver.com/news/energy-surcharge-june-2026",
        title="Twin Oaks families caught off guard by secret energy surcharge",
        published_at=_dt("2026-06-03T15:45:00"),
        text=(
            "Families in Twin Oaks are filing complaints after discovering a secret energy surcharge on bills totaling up to $45 per month. "
            "The charge was not announced in any billing notification. "
            "Utility spokesperson Dana Cho said the company 'followed all applicable state procedures.' "
            "Local councilwoman Brenda Park has called for an independent audit."
        ),
        entities=["Twin Oaks", "Dana Cho", "Brenda Park"],
        phrases=["secret energy surcharge", "billing notification", "independent audit"],
    ),
    # --- National news (2026-06-04) ---
    Document(
        id="doc_011",
        source_name="The National Tribune",
        source_type="national_news",
        url="https://nationaltribune.com/energy/2026/06/04/secret-energy-surcharge-states",
        title="Secret energy surcharge complaints spread across a dozen states",
        published_at=_dt("2026-06-04T06:00:00"),
        text=(
            "A pattern of consumer complaints about a secret energy surcharge has emerged across twelve states, prompting federal attention. "
            "The Federal Energy Regulatory Commission confirmed it is monitoring the situation. "
            "Utilities in five states have defended the charge as a routine grid modernization cost. "
            "Consumer groups argue the roll-out timing and uniformity are suspicious."
        ),
        entities=["Federal Energy Regulatory Commission", "FERC", "twelve states"],
        phrases=["secret energy surcharge", "FERC", "grid modernization cost"],
    ),
    Document(
        id="doc_012",
        source_name="American Policy Wire",
        source_type="national_news",
        url="https://americanpolicywire.com/energy/secret-surcharge-2026-06-04",
        title="Is the secret energy surcharge a coordinated policy rollout?",
        published_at=_dt("2026-06-04T09:30:00"),
        text=(
            "Analysts are asking whether a secret energy surcharge appearing simultaneously across multiple utilities reflects a coordinated policy decision. "
            "The timing aligns with a federal grid resilience directive issued in March. "
            "Utilities are not required to disclose cost pass-through charges separately under current rules. "
            "Senator Dana Okafor has requested a FERC briefing."
        ),
        entities=["FERC", "Senator Dana Okafor", "grid resilience directive"],
        phrases=["secret energy surcharge", "policy rollout", "FERC briefing"],
    ),
    Document(
        id="doc_013",
        source_name="Energy Policy Journal",
        source_type="national_news",
        url="https://energypolicyjournal.com/analysis/backdoor-tax-power-bills-2026",
        title="The backdoor tax on power bills: what regulators are not saying",
        published_at=_dt("2026-06-04T13:00:00"),
        text=(
            "What some are calling a backdoor tax on power bills has attracted regulatory scrutiny after weeks of consumer complaints. "
            "The charge appears to originate in a little-known provision of the 2024 Grid Modernization Act. "
            "Utilities have broad latitude to recover infrastructure costs without itemized consumer notice. "
            "Critics call this a backdoor tax on power bills that bypasses legislative approval."
        ),
        entities=["Grid Modernization Act", "utilities", "regulators"],
        phrases=["backdoor tax on power bills", "Grid Modernization Act", "infrastructure cost recovery"],
    ),
    Document(
        id="doc_014",
        source_name="Reuters Energy Desk",
        source_type="national_news",
        url="https://reuters.com/energy/backdoor-power-bill-tax-2026-06-04",
        title="Utilities defend charges critics call a backdoor tax on power bills",
        published_at=_dt("2026-06-04T16:30:00"),
        text=(
            "Major utility companies pushed back Thursday against characterizations of a recent charge as a backdoor tax on power bills. "
            "Executives said the fee funds mandatory grid upgrades required under federal safety standards. "
            "Consumer advocates countered that the term 'backdoor tax on power bills' accurately reflects the lack of transparency. "
            "The debate is expected to reach congressional hearings next month."
        ),
        entities=["utility executives", "consumer advocates", "Congress"],
        phrases=["backdoor tax on power bills", "grid upgrades", "federal safety standards"],
    ),
    Document(
        id="doc_015",
        source_name="The Washington Examiner",
        source_type="national_news",
        url="https://washingtonexaminer.com/energy/2026/06/04/backdoor-tax-power",
        title="Administration silent on backdoor tax on power bills controversy",
        published_at=_dt("2026-06-04T19:00:00"),
        text=(
            "The White House has not commented on the growing controversy over what consumer groups are calling a backdoor tax on power bills. "
            "Press secretary Alicia Morales declined to address the charge in Thursday's briefing. "
            "Republican senators have circulated a letter demanding the DOE clarify the policy basis for the charge. "
            "Democrats have been split on the issue."
        ),
        entities=["White House", "Alicia Morales", "DOE", "Republican senators"],
        phrases=["backdoor tax on power bills", "DOE", "White House"],
    ),
    # --- Commentary (2026-06-05) ---
    Document(
        id="doc_016",
        source_name="The Energy Critic",
        source_type="commentary",
        url="https://theenergycritic.substack.com/p/backdoor-tax-power-bills-anatomy",
        title="Anatomy of the backdoor tax on power bills",
        published_at=_dt("2026-06-05T08:00:00"),
        text=(
            "The so-called backdoor tax on power bills is less a conspiracy and more a structural failure in utility disclosure rules. "
            "Regulators have long permitted cost-pass-through without itemized notice to consumers. "
            "The current controversy is a predictable consequence of opacity in rate-setting. "
            "We need wholesale reform of how infrastructure costs are communicated, not just this charge."
        ),
        entities=["utility disclosure rules", "regulators", "cost-pass-through"],
        phrases=["backdoor tax on power bills", "utility disclosure", "rate-setting opacity"],
    ),
    Document(
        id="doc_017",
        source_name="Civic Power Review",
        source_type="commentary",
        url="https://civicpowerreview.com/commentary/backdoor-tax-overreach-2026",
        title="The backdoor tax narrative is real — but the framing misses the point",
        published_at=_dt("2026-06-05T12:30:00"),
        text=(
            "Critics are right that the backdoor tax on power bills framing captures genuine consumer frustration. "
            "But the term may overstate intentionality — utilities acted within the law, even if the law is inadequate. "
            "The real problem is that state commissions lack the resources to audit all infrastructure surcharges in real time. "
            "The solution is regulatory capacity, not outrage."
        ),
        entities=["state commissions", "utilities", "infrastructure surcharges"],
        phrases=["backdoor tax on power bills", "state commission", "regulatory capacity"],
    ),
    Document(
        id="doc_018",
        source_name="Consumer Finance Tribune",
        source_type="commentary",
        url="https://consumerfinancetribune.com/energy/ratepayer-burden-analysis-2026",
        title="The ratepayer burden is the story the energy industry hoped you wouldn't notice",
        published_at=_dt("2026-06-05T16:00:00"),
        text=(
            "What started as forum posts about a hidden charge has crystallized into a clear policy story: the ratepayer burden is growing, quietly and without democratic accountability. "
            "Ratepayers across the country are absorbing grid modernization costs that were never put to a vote. "
            "The utilities call it a fee. Regulators call it authorized. Consumers call it a ratepayer burden they never agreed to. "
            "The terminology may differ but the math is the same."
        ),
        entities=["ratepayers", "utilities", "regulators", "grid modernization"],
        phrases=["ratepayer burden", "grid modernization", "democratic accountability"],
    ),
    # --- Speech transcripts (2026-06-06) ---
    Document(
        id="doc_019",
        source_name="Senate Energy Committee Hearing",
        source_type="speech_transcript",
        url="https://congress.gov/hearings/energy/2026-06-06/sen-okafor-statement",
        title="Senator Okafor opening statement — Energy Committee hearing on ratepayer burden",
        published_at=_dt("2026-06-06T10:00:00"),
        text=(
            "The ratepayer burden we are discussing today is not a hypothetical. "
            "American families are paying more on their power bills right now — without any notice, without any vote, and without any clear explanation from the utilities profiting from these charges. "
            "This committee will get answers. "
            "The ratepayer burden ends when we restore transparency to rate-setting."
        ),
        entities=["Senator Okafor", "Senate Energy Committee", "utilities", "American families"],
        phrases=["ratepayer burden", "Senate Energy Committee", "rate-setting transparency"],
    ),
    Document(
        id="doc_020",
        source_name="Consumer Advocacy Summit 2026",
        source_type="speech_transcript",
        url="https://consumeradvocacysummit.org/2026/transcripts/keynote-ratepayer-burden",
        title="Keynote address: ending the ratepayer burden",
        published_at=_dt("2026-06-06T14:30:00"),
        text=(
            "Six days ago, a handful of forum posts raised the alarm about what was then called a hidden charge. "
            "Today it has a name: the ratepayer burden. "
            "It has reached a Senate hearing room, and it will reach every state legislature that cares about consumer protection. "
            "Our job now is to make sure the ratepayer burden is remembered as the moment transparency won."
        ),
        entities=["ratepayer burden", "Senate", "state legislature", "consumer protection"],
        phrases=["ratepayer burden", "consumer protection", "transparency"],
    ),
]

# ---------------------------------------------------------------------------
# CORPUS — Immigration Talking Point (8 documents)
# ---------------------------------------------------------------------------

DEMO_DOCUMENTS_IMMIGRATION: list[Document] = [
    Document(
        id="imm_001",
        source_name="r/BorderDebate",
        source_type="forum",
        url="https://reddit.com/r/borderdebate/comments/imm001",
        title="The same talking point about immigration costs keeps getting recycled",
        published_at=_dt("2026-06-02T09:00:00"),
        text=(
            "I noticed the exact same phrase about immigration costs showing up across at least a dozen accounts in the last 48 hours. "
            "It is almost word-for-word identical: 'immigrants cost taxpayers $X billion annually.' "
            "The number changes but the sentence structure does not. "
            "Someone is running a coordinated message."
        ),
        entities=["immigration", "taxpayers"],
        phrases=["immigration cost talking point", "coordinated message"],
    ),
    Document(
        id="imm_002",
        source_name="PolicyFactCheck.org",
        source_type="blog",
        url="https://policyfactcheck.org/blog/2026/06/02/immigration-cost-claim-debunked",
        title="The '$180 billion' immigration cost claim: where does it come from?",
        published_at=_dt("2026-06-02T14:00:00"),
        text=(
            "A claim that immigration costs taxpayers $180 billion annually has spread across social media and two cable news segments this week. "
            "The figure appears to originate from a 2019 report by the Heritage Foundation, not a current government estimate. "
            "Multiple economists dispute the methodology. "
            "We rate this claim as misleading."
        ),
        entities=["Heritage Foundation", "economists", "cable news"],
        phrases=["immigration cost claim", "$180 billion", "Heritage Foundation"],
    ),
    Document(
        id="imm_003",
        source_name="Eastdale Tribune",
        source_type="local_news",
        url="https://eastdaletribune.com/news/2026/06/03/immigration-claim-local-reaction",
        title="Local officials push back on viral immigration cost figure",
        published_at=_dt("2026-06-03T08:00:00"),
        text=(
            "Eastdale city council members are reacting to a viral claim about immigration costs circulating on social media. "
            "Council member Rosa Jimenez called the figure 'without factual basis in our community.' "
            "The claim has appeared in letters to the editor at three local papers this week. "
            "No credentialed source has confirmed the number."
        ),
        entities=["Rosa Jimenez", "Eastdale", "local papers"],
        phrases=["immigration cost claim", "local reaction", "viral claim"],
    ),
    Document(
        id="imm_004",
        source_name="National Policy Tribune",
        source_type="national_news",
        url="https://nationalpolicytribune.com/2026/06/04/immigration-talking-point-spread",
        title="How an outdated immigration cost figure became a 2026 talking point",
        published_at=_dt("2026-06-04T10:00:00"),
        text=(
            "A six-year-old report figure has re-emerged as a central immigration talking point in the 2026 election cycle. "
            "Researchers tracked the phrase across 4,200 social media posts and 14 congressional speeches in the past week. "
            "The talking point has been amplified by at least three PAC-affiliated accounts. "
            "Fact-checkers have consistently rated it as lacking current evidentiary support."
        ),
        entities=["2026 election", "PAC", "congressional speeches", "fact-checkers"],
        phrases=["immigration talking point", "PAC amplification", "election cycle"],
    ),
    Document(
        id="imm_005",
        source_name="The Fiscal Observer",
        source_type="national_news",
        url="https://fiscalobserver.com/2026/06/04/immigration-cost-debate",
        title="Economists divided on immigration cost claims entering policy debate",
        published_at=_dt("2026-06-04T15:30:00"),
        text=(
            "Immigration economists are divided on how to respond to the resurgent immigration cost talking point. "
            "Some argue engaging the claim lends it credibility. "
            "Others say silence allows misinformation to fill the vacuum. "
            "The CBO has not released an updated estimate since 2021."
        ),
        entities=["CBO", "economists", "immigration economists"],
        phrases=["immigration cost talking point", "CBO estimate", "misinformation"],
    ),
    Document(
        id="imm_006",
        source_name="ImmigrationWatch Commentary",
        source_type="commentary",
        url="https://immigrationwatch.net/commentary/talking-point-anatomy-2026",
        title="The anatomy of the 2026 immigration talking point",
        published_at=_dt("2026-06-05T09:00:00"),
        text=(
            "The immigration talking point circulating this cycle follows a familiar structure: cite an old number, strip the context, repeat at scale. "
            "What is new is the speed of amplification — the claim reached national news in under 72 hours from its first forum appearance. "
            "This compression of the misinformation cycle is the real story. "
            "The number itself is secondary."
        ),
        entities=["misinformation cycle", "national news", "forum"],
        phrases=["immigration talking point", "misinformation cycle", "amplification speed"],
    ),
    Document(
        id="imm_007",
        source_name="DemocracyNow! Commentary",
        source_type="commentary",
        url="https://democracynow.org/commentary/2026/06/05/immigration-claim-counter",
        title="Counter-narrative: what the immigration cost data actually shows",
        published_at=_dt("2026-06-05T13:00:00"),
        text=(
            "A coalition of economists released a counter-statement to the immigration cost talking point, citing CBO and NAS data showing net positive fiscal contributions over 25-year windows. "
            "The counter-statement received a fraction of the attention the original claim did. "
            "This asymmetry is typical: corrections travel slower than claims. "
            "The counter-narrative reached approximately 12 percent of the audience the original claim did."
        ),
        entities=["CBO", "NAS", "economists coalition"],
        phrases=["immigration cost counter-narrative", "CBO", "NAS data"],
    ),
    Document(
        id="imm_008",
        source_name="House Judiciary Committee Hearing",
        source_type="speech_transcript",
        url="https://congress.gov/hearings/judiciary/2026-06-06/rep-torres-statement",
        title="Rep. Torres statement — House Judiciary hearing on immigration policy",
        published_at=_dt("2026-06-06T11:00:00"),
        text=(
            "The immigration talking point my colleagues keep citing was thoroughly debunked by CBO analysts three years ago. "
            "We should not be setting policy on the basis of a recycled number that no credentialed economist defends. "
            "I urge this committee to request an updated CBO immigration cost analysis before this debate goes further. "
            "The American people deserve accurate numbers, not a talking point."
        ),
        entities=["Rep. Torres", "House Judiciary Committee", "CBO", "Congress"],
        phrases=["immigration talking point", "CBO analysis", "House Judiciary"],
    ),
]

ALL_DOCUMENTS: list[Document] = DEMO_DOCUMENTS + DEMO_DOCUMENTS_IMMIGRATION

# ---------------------------------------------------------------------------
# DEMO NARRATIVES — pre-computed clusters
# ---------------------------------------------------------------------------

DEMO_NARRATIVES: list[NarrativeCluster] = [
    NarrativeCluster(
        id="narrative_001",
        label="Hidden Energy Tax",
        canonical_phrases=[
            "hidden energy tax",
            "secret energy surcharge",
            "backdoor tax on power bills",
            "ratepayer burden",
        ],
        mutation_trail=[
            MutationEntry(
                phrase="hidden energy tax",
                first_doc_id="doc_001",
                timestamp=_dt("2026-06-01T08:14:00"),
                source_type="forum",
            ),
            MutationEntry(
                phrase="secret energy surcharge",
                first_doc_id="doc_007",
                timestamp=_dt("2026-06-03T07:30:00"),
                source_type="local_news",
            ),
            MutationEntry(
                phrase="backdoor tax on power bills",
                first_doc_id="doc_013",
                timestamp=_dt("2026-06-04T13:00:00"),
                source_type="national_news",
            ),
            MutationEntry(
                phrase="ratepayer burden",
                first_doc_id="doc_018",
                timestamp=_dt("2026-06-05T16:00:00"),
                source_type="commentary",
            ),
        ],
        first_observed_doc_id="doc_001",
        first_observed_at=_dt("2026-06-01T08:14:00"),
        document_ids=[f"doc_{i:03d}" for i in range(1, 21)],
        spike_score=4.2,
        confidence=0.87,
        source_type_breakdown={
            "forum": 3,
            "blog": 3,
            "local_news": 4,
            "national_news": 5,
            "commentary": 3,
            "speech_transcript": 2,
        },
    ),
    NarrativeCluster(
        id="narrative_002",
        label="Immigration Talking Point",
        canonical_phrases=[
            "immigration cost talking point",
            "immigration cost claim",
            "immigration talking point",
        ],
        mutation_trail=[
            MutationEntry(
                phrase="immigration cost talking point",
                first_doc_id="imm_001",
                timestamp=_dt("2026-06-02T09:00:00"),
                source_type="forum",
            ),
            MutationEntry(
                phrase="immigration cost claim",
                first_doc_id="imm_002",
                timestamp=_dt("2026-06-02T14:00:00"),
                source_type="blog",
            ),
            MutationEntry(
                phrase="immigration talking point",
                first_doc_id="imm_004",
                timestamp=_dt("2026-06-04T10:00:00"),
                source_type="national_news",
            ),
        ],
        first_observed_doc_id="imm_001",
        first_observed_at=_dt("2026-06-02T09:00:00"),
        document_ids=[f"imm_{i:03d}" for i in range(1, 9)],
        spike_score=2.1,
        confidence=0.73,
        source_type_breakdown={
            "forum": 1,
            "blog": 1,
            "local_news": 1,
            "national_news": 2,
            "commentary": 2,
            "speech_transcript": 1,
        },
    ),
]

# ---------------------------------------------------------------------------
# DEMO REPORTS — pre-written full investigation reports
# ---------------------------------------------------------------------------

DEMO_REPORTS: dict[str, InvestigationReport] = {
    "narrative_001": InvestigationReport(
        id="report_001",
        cluster_id="narrative_001",
        generated_at=_dt("2026-06-06T18:00:00"),
        narrative_title="Hidden Energy Tax",
        summary=(
            "A narrative about undisclosed utility charges originated in consumer forums on June 1, 2026, "
            "and spread to local news, national outlets, and a Senate hearing within six days. "
            "The phrase evolved from 'hidden energy tax' to 'secret energy surcharge,' then 'backdoor tax on power bills,' "
            "and finally 'ratepayer burden' as the story moved from citizen complaint to political language. "
            "The spread pattern is consistent with grassroots amplification rather than top-down coordination, "
            "though the simultaneous emergence of similar charges across multiple utilities warrants regulatory scrutiny."
        ),
        spread_pattern="grassroots",
        first_observed={
            "doc_id": "doc_001",
            "source": "r/EnergyConsumers",
            "source_type": "forum",
            "timestamp": "2026-06-01T08:14:00Z",
            "phrase": "hidden energy tax",
            "url": "https://reddit.com/r/energyconsumers/comments/abc001",
        },
        mutation_trail=[
            MutationEntry(
                phrase="hidden energy tax",
                first_doc_id="doc_001",
                timestamp=_dt("2026-06-01T08:14:00"),
                source_type="forum",
            ),
            MutationEntry(
                phrase="secret energy surcharge",
                first_doc_id="doc_007",
                timestamp=_dt("2026-06-03T07:30:00"),
                source_type="local_news",
            ),
            MutationEntry(
                phrase="backdoor tax on power bills",
                first_doc_id="doc_013",
                timestamp=_dt("2026-06-04T13:00:00"),
                source_type="national_news",
            ),
            MutationEntry(
                phrase="ratepayer burden",
                first_doc_id="doc_018",
                timestamp=_dt("2026-06-05T16:00:00"),
                source_type="commentary",
            ),
        ],
        coordination_signals=[
            "Similar charge amounts ($28–$45) reported across geographically distant utilities within the same billing cycle.",
            "Multiple utilities used nearly identical label terms ('System Reliability Fee', 'Grid Modernization Fee') without prior public coordination.",
            "Rollout timing aligns with a March 2026 federal grid resilience directive, suggesting policy-driven simultaneity rather than organic coincidence.",
        ],
        counter_signals=[
            "FERC confirmed the charges were individually authorized under existing utility rate structures.",
            "Consumer Finance Tribune commentary noted the utilities acted within the law, even if disclosure was inadequate.",
            "No direct evidence of inter-utility communication coordinating the charge rollout.",
        ],
        evidence=[
            EvidenceItem(
                doc_id="doc_001",
                source="r/EnergyConsumers",
                source_type="forum",
                timestamp=_dt("2026-06-01T08:14:00"),
                excerpt="Looks like there is a hidden energy tax that the utility is not publicizing.",
                reason="First known use of the phrase 'hidden energy tax' in this narrative cluster.",
                verified=True,
                verification_status="verified",
            ),
            EvidenceItem(
                doc_id="doc_007",
                source="Springfield Gazette",
                source_type="local_news",
                timestamp=_dt("2026-06-03T07:30:00"),
                excerpt="Residents report secret energy surcharge on monthly bills.",
                reason="First mainstream local news pickup. Phrase mutation from 'hidden energy tax' to 'secret energy surcharge' confirmed here.",
                verified=True,
                verification_status="verified",
            ),
            EvidenceItem(
                doc_id="doc_013",
                source="Energy Policy Journal",
                source_type="national_news",
                timestamp=_dt("2026-06-04T13:00:00"),
                excerpt="Critics call this a backdoor tax on power bills that bypasses legislative approval.",
                reason="First national-level framing shift to 'backdoor tax on power bills.'",
                verified=False,
                verification_status="metadata_mismatch",
            ),
            EvidenceItem(
                doc_id="doc_019",
                source="Senate Energy Committee Hearing",
                source_type="speech_transcript",
                timestamp=_dt("2026-06-06T10:00:00"),
                excerpt="The ratepayer burden we are discussing today is not a hypothetical.",
                reason="Phrase entered official legislative language — a key escalation signal.",
                verified=False,
                verification_status="unavailable",
            ),
        ],
        confidence=0.87,
        limitations=[
            "Analysis is limited to 20 documents in a seeded corpus. A production system would search a live document index.",
            "Phrase similarity detection uses string matching, not semantic embeddings. Some mutations may be missed.",
            "No cross-platform social data (Twitter/X, Facebook) is included in this corpus.",
            "Coordination signals are based on timing and label similarity; causation cannot be established without internal utility communications.",
        ],
        recommended_human_checks=[
            "Verify whether the March 2026 grid resilience directive explicitly permitted utilities to pass costs to consumers without itemized notice.",
            "Request internal communications from at least two utilities to assess whether the simultaneous rollout was coordinated.",
            "Confirm Senate Energy Committee hearing date and Senator Okafor's full statement via official congressional record.",
            "Contact Marcus Bell (Riverside County energy attorney) for expert comment on regulatory disclosure obligations.",
        ],
        cached=False,
    ),
    "narrative_002": InvestigationReport(
        id="report_002",
        cluster_id="narrative_002",
        generated_at=_dt("2026-06-06T18:30:00"),
        narrative_title="Immigration Talking Point",
        summary=(
            "A recycled immigration cost figure originated in online forums on June 2, 2026, "
            "and reached national news and a congressional hearing within four days. "
            "The claim traces to a 2019 Heritage Foundation report. "
            "Fact-checkers and economists have consistently rated the claim as misleading or lacking current support. "
            "A counter-narrative from an economists coalition reached approximately 12 percent of the original claim's audience."
        ),
        spread_pattern="reactive_amplification",
        first_observed={
            "doc_id": "imm_001",
            "source": "r/BorderDebate",
            "source_type": "forum",
            "timestamp": "2026-06-02T09:00:00Z",
            "phrase": "immigration cost talking point",
            "url": "https://reddit.com/r/borderdebate/comments/imm001",
        },
        mutation_trail=[
            MutationEntry(
                phrase="immigration cost talking point",
                first_doc_id="imm_001",
                timestamp=_dt("2026-06-02T09:00:00"),
                source_type="forum",
            ),
            MutationEntry(
                phrase="immigration talking point",
                first_doc_id="imm_004",
                timestamp=_dt("2026-06-04T10:00:00"),
                source_type="national_news",
            ),
        ],
        coordination_signals=[
            "At least three PAC-affiliated accounts amplified the claim in the first 24 hours.",
            "Claim appeared in 14 congressional speeches within one week — unusual volume for a single figure.",
        ],
        counter_signals=[
            "CBO and NAS data cited by economist coalition show net positive fiscal contributions over 25-year windows.",
            "Rep. Torres called the figure 'thoroughly debunked' by CBO analysts in 2023.",
            "PolicyFactCheck.org rated the claim 'misleading' with sourcing breakdown.",
        ],
        evidence=[
            EvidenceItem(
                doc_id="imm_001",
                source="r/BorderDebate",
                source_type="forum",
                timestamp=_dt("2026-06-02T09:00:00"),
                excerpt="Someone is running a coordinated message.",
                reason="First observation of coordinated amplification signal in this narrative.",
                verified=True,
                verification_status="verified",
            ),
            EvidenceItem(
                doc_id="imm_004",
                source="National Policy Tribune",
                source_type="national_news",
                timestamp=_dt("2026-06-04T10:00:00"),
                excerpt="The talking point has been amplified by at least three PAC-affiliated accounts.",
                reason="First national-level identification of PAC amplification.",
                verified=True,
                verification_status="verified",
            ),
        ],
        confidence=0.73,
        limitations=[
            "Corpus is limited to 8 documents. Counter-narrative reach estimates are from a single source.",
            "PAC account identification was reported by National Policy Tribune, not independently verified in this corpus.",
        ],
        recommended_human_checks=[
            "Request updated CBO immigration cost analysis to establish current evidentiary baseline.",
            "Identify and audit the three PAC-affiliated accounts mentioned in National Policy Tribune.",
        ],
        cached=False,
    ),
}
# ---------------------------------------------------------------------------
# DEMO GRAPHS — pre-built narrative graphs
# ---------------------------------------------------------------------------

DEMO_GRAPHS: dict[str, NarrativeGraph] = {
    "narrative_001": NarrativeGraph(
        narrative_id="narrative_001",
        nodes=[
            GraphNode(id="doc_001", label="r/EnergyConsumers", source_type="forum", timestamp=_dt("2026-06-01T08:14:00"), title="Anyone else notice the hidden energy tax?", url="https://reddit.com/r/energyconsumers/comments/abc001", phrase_used="hidden energy tax"),
            GraphNode(id="doc_002", label="EnergyWatchdog Forums", source_type="forum", timestamp=_dt("2026-06-01T11:45:00"), title="The hidden energy tax nobody is talking about", url="https://energywatchdog.net/forum/t/hidden-energy-tax-2026", phrase_used="hidden energy tax"),
            GraphNode(id="doc_003", label="ClimateSkepticBoard", source_type="forum", timestamp=_dt("2026-06-01T17:22:00"), title="Government-coordinated hidden energy tax", url="https://climateskepticboard.com/posts/hidden-energy-tax-rollout", phrase_used="hidden energy tax"),
            GraphNode(id="doc_004", label="TheIndependentVoice.blog", source_type="blog", timestamp=_dt("2026-06-02T09:00:00"), title="What is the hidden energy tax?", url="https://theindependentvoice.blog/2026/06/02/hidden-energy-tax-explained", phrase_used="hidden energy tax"),
            GraphNode(id="doc_005", label="RatepayerRights.org", source_type="blog", timestamp=_dt("2026-06-02T13:30:00"), title="Tracking the hidden energy tax", url="https://ratepayerrights.org/blog/2026/06/hidden-energy-tax-tracker", phrase_used="hidden energy tax"),
            GraphNode(id="doc_006", label="ConsumerPowerBlog", source_type="blog", timestamp=_dt("2026-06-02T16:00:00"), title="Is the hidden energy tax real?", url="https://consumerpowerblog.net/2026/06/02/is-the-hidden-energy-tax-real", phrase_used="hidden energy tax"),
            GraphNode(id="doc_007", label="Springfield Gazette", source_type="local_news", timestamp=_dt("2026-06-03T07:30:00"), title="Residents report secret energy surcharge", url="https://springfieldgazette.com/news/2026/06/03/secret-energy-surcharge-investigation", phrase_used="secret energy surcharge"),
            GraphNode(id="doc_008", label="Riverside County Herald", source_type="local_news", timestamp=_dt("2026-06-03T10:00:00"), title="What is the secret energy surcharge?", url="https://riversidecountyherald.com/2026/06/03/secret-energy-surcharge", phrase_used="secret energy surcharge"),
            GraphNode(id="doc_013", label="Energy Policy Journal", source_type="national_news", timestamp=_dt("2026-06-04T13:00:00"), title="The backdoor tax on power bills", url="https://energypolicyjournal.com/analysis/backdoor-tax-power-bills-2026", phrase_used="backdoor tax on power bills"),
            GraphNode(id="doc_019", label="Senate Energy Committee", source_type="speech_transcript", timestamp=_dt("2026-06-06T10:00:00"), title="Sen. Okafor statement — ratepayer burden", url="https://congress.gov/hearings/energy/2026-06-06/sen-okafor-statement", phrase_used="ratepayer burden"),
        ],
        edges=[
            GraphEdge(source="doc_001", target="doc_002", edge_type="phrase_reuse", weight=0.95, evidence="Both use 'hidden energy tax' within 3.5 hours on the same forum day.", time_delta_hours=3.52),
            GraphEdge(source="doc_002", target="doc_003", edge_type="phrase_reuse", weight=0.92, evidence="'hidden energy tax' reused. Both on forum tier same day.", time_delta_hours=5.62),
            GraphEdge(source="doc_003", target="doc_004", edge_type="temporal_sequence", weight=0.7, evidence="Forum post precedes blog pickup by 15.6 hours. Entity overlap: energy tax, utility.", time_delta_hours=15.63),
            GraphEdge(source="doc_004", target="doc_005", edge_type="phrase_reuse", weight=0.91, evidence="Both blog posts use 'hidden energy tax'. Published same day.", time_delta_hours=4.5),
            GraphEdge(source="doc_006", target="doc_007", edge_type="phrase_mutation", weight=0.72, evidence="'hidden energy tax' → 'secret energy surcharge'. Similarity 0.61.", time_delta_hours=15.5),
            GraphEdge(source="doc_007", target="doc_008", edge_type="entity_overlap", weight=0.8, evidence="Shared entities: utility bill, surcharge complaints. Within 2.5 hours.", time_delta_hours=2.5),
            GraphEdge(source="doc_008", target="doc_013", edge_type="phrase_mutation", weight=0.68, evidence="'secret energy surcharge' → 'backdoor tax on power bills'. Similarity 0.44.", time_delta_hours=27.0),
            GraphEdge(source="doc_013", target="doc_019", edge_type="phrase_mutation", weight=0.65, evidence="'backdoor tax on power bills' → 'ratepayer burden'. Narrative enters legislative language.", time_delta_hours=45.0),
        ],
    ),
    "narrative_002": NarrativeGraph(
        narrative_id="narrative_002",
        nodes=[
            GraphNode(id="imm_001", label="r/BorderDebate", source_type="forum", timestamp=_dt("2026-06-02T09:00:00"), title="Same talking point keeps getting recycled", url="https://reddit.com/r/borderdebate/comments/imm001", phrase_used="immigration cost talking point"),
            GraphNode(id="imm_002", label="PolicyFactCheck.org", source_type="blog", timestamp=_dt("2026-06-02T14:00:00"), title="The '$180 billion' claim debunked", url="https://policyfactcheck.org/blog/2026/06/02/immigration-cost-claim-debunked", phrase_used="immigration cost claim"),
            GraphNode(id="imm_004", label="National Policy Tribune", source_type="national_news", timestamp=_dt("2026-06-04T10:00:00"), title="How an outdated figure became a talking point", url="https://nationalpolicytribune.com/2026/06/04/immigration-talking-point-spread", phrase_used="immigration talking point"),
            GraphNode(id="imm_008", label="House Judiciary Committee", source_type="speech_transcript", timestamp=_dt("2026-06-06T11:00:00"), title="Rep. Torres statement", url="https://congress.gov/hearings/judiciary/2026-06-06/rep-torres-statement", phrase_used="immigration talking point"),
        ],
        edges=[
            GraphEdge(source="imm_001", target="imm_002", edge_type="temporal_sequence", weight=0.75, evidence="Forum claim precedes fact-check response by 5 hours.", time_delta_hours=5.0),
            GraphEdge(source="imm_002", target="imm_004", edge_type="phrase_mutation", weight=0.7, evidence="'immigration cost claim' → 'immigration talking point'. Similarity 0.58.", time_delta_hours=44.0),
            GraphEdge(source="imm_004", target="imm_008", edge_type="temporal_sequence", weight=0.8, evidence="National news coverage precedes congressional hearing statement by 49 hours.", time_delta_hours=49.0),
        ],
    ),
}

