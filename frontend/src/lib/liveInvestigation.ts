import type {
  InvestigationConfidence,
  InvestigationEdge,
  InvestigationExperience,
  InvestigationFlowchartData,
  InvestigationNode,
  InvestigationReceipt,
  InvestigationNodeSource,
  LiveClaimCounterpointPair,
  LiveCounterNarrative,
  LiveDocument,
  LiveFinalReportClaim,
  LiveGapItem,
  LiveSourceDiversityResult,
  LiveInvestigationWorkspace,
  LiveTimelineEvent,
} from "../types/rhetoriq";

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
});

const TIME_FORMATTER = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
});

export function buildInvestigationExperienceFromWorkspace(
  workspace: LiveInvestigationWorkspace,
): InvestigationExperience {
  const report = workspace.report;
  const loopConfidence =
    workspace.research_loop?.confidence_dimensions.synthesis_confidence.score;
  const title =
    report?.report_title ??
    workspace.query_text ??
    `${workspace.plan?.topic ?? "Live"} Investigation`;
  const summary =
    report?.report_summary ??
    workspace.analyst?.draft_report_sections.executive_summary ??
    workspace.timeline?.timeline_summary ??
    "RhetoriQ is building the investigation from the current evidence set.";

  return {
    confidence: toDisplayConfidence(
      report?.confidence_label ??
        (loopConfidence !== undefined
          ? loopConfidence >= 0.72
            ? "high"
            : loopConfidence >= 0.46
              ? "medium"
              : "low"
          : undefined) ??
        workspace.analyst?.confidence_label ??
        workspace.timeline?.confidence_label ??
        "unknown",
    ),
    firstObserved: getFirstObservedLabel(workspace),
    flowchartData: buildFlowchartData(workspace),
    generatedAt: `Updated ${formatDateTime(workspace.updated_at)}`,
    id: workspace.investigation_id,
    kicker: "Live investigation workspace",
    receiptCount: countWorkspaceReceipts(workspace),
    sourceCount:
      workspace.retrieval?.coverage_summary.total_documents ??
      workspace.retrieved_documents.length,
    status: formatStatus(workspace.status),
    summary,
    title,
  };
}

export function getRecommendedChecks(workspace: LiveInvestigationWorkspace) {
  return workspace.report?.recommended_human_checks ??
    workspace.analyst?.recommended_human_checks ??
    [];
}

export function getRivalHypotheses(workspace: LiveInvestigationWorkspace) {
  return workspace.plan?.rival_hypotheses ?? [];
}

export function getPassHistory(workspace: LiveInvestigationWorkspace) {
  return workspace.research_loop?.pass_history ?? [];
}

export function getRetryHistory(workspace: LiveInvestigationWorkspace) {
  return workspace.research_loop?.retry_history ?? [];
}

export function getOpenGaps(workspace: LiveInvestigationWorkspace): LiveGapItem[] {
  return (workspace.gap_ledger?.entries ?? workspace.gap_analysis?.missing_evidence ?? []).filter(
    (gap) => gap.status === "open",
  );
}

export function getResolvedGaps(workspace: LiveInvestigationWorkspace): LiveGapItem[] {
  return (workspace.gap_ledger?.entries ?? []).filter((gap) => gap.status === "resolved");
}

export function getClaimLedgerEntries(workspace: LiveInvestigationWorkspace) {
  return workspace.claim_ledger?.entries ?? [];
}

export function getTopClaims(workspace: LiveInvestigationWorkspace) {
  return workspace.report?.key_claims ?? [];
}

export function getLimitations(workspace: LiveInvestigationWorkspace) {
  const values = [
    ...(workspace.report?.limitations ?? []),
    ...(workspace.receipts?.limitations ?? []),
    ...(workspace.narrative_family?.limitations ?? []),
    ...(workspace.agent_debate?.limitations ?? []),
    ...(workspace.provenance_trace?.provenance_limitations ?? []),
    ...(workspace.analyst?.limitations ?? []),
    ...(workspace.timeline?.limitations ?? []),
    ...(workspace.counter_narratives?.limitations ?? []),
    ...(workspace.claim_counterpoints?.limitations ?? []),
  ];

  return Array.from(new Set(values));
}

export function getSearchWarnings(workspace: LiveInvestigationWorkspace) {
  return workspace.retrieval?.warnings ?? [];
}

export function getCoverageHighlights(workspace: LiveInvestigationWorkspace) {
  const coverage = workspace.retrieval?.coverage_summary;
  if (!coverage) {
    return [];
  }

  return [
    `${coverage.total_documents} documents`,
    `${coverage.unique_sources} unique sources`,
    `${coverage.search_rounds_completed} search rounds`,
    `${Object.keys(coverage.lane_distribution ?? {}).length} retrieval lanes`,
  ];
}

export function getSourceDiversityHighlights(workspace: LiveInvestigationWorkspace) {
  const diversity = workspace.source_diversity;
  if (!diversity) {
    return [];
  }

  return [
    `${diversity.classified_documents}/${diversity.total_documents} docs classified`,
    summarizeDistribution(diversity.source_type_distribution),
    summarizeDistribution(diversity.institution_distribution),
  ].filter(Boolean) as string[];
}

export function getSourceDiversityFindings(workspace: LiveInvestigationWorkspace) {
  return workspace.source_diversity?.findings ?? [];
}

export function getSourceDiversityCaveat(diversity: LiveSourceDiversityResult | null | undefined) {
  if (!diversity) {
    return null;
  }
  const unknownInstitutionCount = diversity.institution_distribution.unknown ?? 0;
  if (unknownInstitutionCount > 0) {
    return `Source diversity provides context about the observed dataset. ${unknownInstitutionCount} source labels remain unknown, so this is not a truth score or moral judgment.`;
  }
  return "Source diversity provides context about the observed dataset. It is not a truth score or moral judgment.";
}

export function getStageLabel(workspace: LiveInvestigationWorkspace) {
  switch (workspace.current_stage) {
    case "planner":
      return "Planner completed";
    case "retriever":
      return "Retrieval completed";
    case "source_diversity":
      return "Source diversity built";
    case "timeline":
      return "Timeline built";
    case "counter_narrative":
      return "Counter-narratives built";
    case "narrative_family":
      return "Narrative family built";
    case "gap_analysis":
      return "Gap analysis built";
    case "provenance":
      return "Provenance traced";
    case "analyst":
      return "Analyst synthesis built";
    case "skeptic":
      return "Skeptic review built";
    case "claim_counterpoint":
      return "Claim counterpoints built";
    case "receipts":
      return "Receipts built";
    case "agent_debate":
      return "Agent debate built";
    case "report":
      return "Final report built";
    case "research_loop":
      return "Research loop completed";
    default:
      return "Investigation in progress";
  }
}

export function formatClaimConfidence(claim: LiveFinalReportClaim) {
  return `${Math.round(claim.confidence_score * 100)}% confidence`;
}

function buildFlowchartData(
  workspace: LiveInvestigationWorkspace,
): InvestigationFlowchartData {
  const currentNodeId = "current-narrative";
  const strongestCounterpoint = getStrongestCounterpointPair(workspace);
  const docsById = new Map(
    workspace.retrieved_documents.map((document) => [document.id, document]),
  );
  const timelineEvents = [...(workspace.timeline?.timeline_events ?? [])].sort(
    (left, right) =>
      new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime(),
  );
  const mainTimelineEvents = timelineEvents.filter(
    (event) => event.narrative_side !== "counter",
  );
  const timelineNodes = (mainTimelineEvents.length > 0 ? mainTimelineEvents : timelineEvents).map(
    (event, index) => toTimelineNode(event, index, docsById, workspace),
  );
  const counterNodes = (workspace.counter_narratives?.counter_narratives ?? [])
    .slice(0, 3)
    .map((item, index) => toCounterNode(item, index, docsById));

  const currentNode: InvestigationNode = {
    id: currentNodeId,
    label: "Current narrative state",
    subtitle:
      workspace.report?.report_title ??
      workspace.query_text ??
      workspace.plan?.canonical_phrase ??
      workspace.plan?.topic ??
      "Latest narrative snapshot",
    nodeType: "current",
    timestamp: formatTime(workspace.updated_at),
    status: "mainstreaming",
    confidence:
      workspace.report?.confidence_label ??
      workspace.analyst?.confidence_label ??
      "unknown",
    sourceCount:
      workspace.retrieval?.coverage_summary.total_documents ??
      workspace.retrieved_documents.length,
    counterSourceCount:
      workspace.counter_narratives?.counter_narratives.reduce(
        (sum, item) => sum + item.supporting_document_ids.length,
        0,
      ) ?? 0,
    receiptCount: countWorkspaceReceipts(workspace),
    summary:
      workspace.report?.report_summary ??
      workspace.analyst?.draft_report_sections.executive_summary ??
      workspace.timeline?.timeline_summary,
    sources: buildCurrentSources(workspace, docsById, strongestCounterpoint),
    receipts: buildCurrentReceipts(workspace, strongestCounterpoint),
  };

  const nodes = [...timelineNodes, ...counterNodes, currentNode];
  const edges = buildEdges(timelineNodes, counterNodes, currentNodeId, workspace);

  return {
    currentNodeId,
    edges,
    nodes,
    query: workspace.query_text,
    title:
      workspace.report?.report_title ??
      `${workspace.plan?.topic ?? "Investigation"} Investigation`,
  };
}

function toTimelineNode(
  event: LiveTimelineEvent,
  index: number,
  docsById: Map<string, LiveDocument>,
  workspace: LiveInvestigationWorkspace,
): InvestigationNode {
  const document = docsById.get(event.document_id);
  const nodeId = `timeline-${event.id}`;
  const counterpoint = getBestCounterpointPairForDocumentIds(workspace, [event.document_id]);
  const baseSources = document ? [toSource(document)] : [];
  const baseReceipts = [toReceipt(event, document)];

  return {
    id: nodeId,
    label: getTimelineDisplayLabel(event, index),
    subtitle: getTimelineDisplaySubtitle(event),
    nodeType: getTimelineNodeType(event),
    timestamp: formatTime(event.timestamp),
    status: index === 0 ? "emerging" : "amplifying",
    confidence: toConfidenceLabel(event.importance_score),
    sourceCount: 1,
    counterSourceCount: counterpoint?.counter_document_ids.length ?? 0,
    receiptCount: baseReceipts.length + (counterpoint?.counter_receipts.length ?? 0),
    summary: counterpoint
      ? `${event.explanation} ${counterpoint.relationship_summary}`
      : event.explanation,
    sources: [
      ...baseSources,
      ...buildCounterSourcesFromPair(counterpoint),
    ],
    receipts: [
      ...baseReceipts,
      ...buildCounterReceiptsFromPair(counterpoint),
    ],
  };
}

function toCounterNode(
  item: LiveCounterNarrative,
  index: number,
  docsById: Map<string, LiveDocument>,
): InvestigationNode {
  const docs = item.supporting_document_ids
    .map((docId) => docsById.get(docId))
    .filter((document): document is LiveDocument => Boolean(document));

  return {
    id: `counter-${item.id}`,
    label: item.title,
    subtitle: item.relationship_to_main_narrative,
    nodeType: "counter_narrative",
    timestamp: item.first_observed_doc_id
      ? formatTime(docsById.get(item.first_observed_doc_id)?.published_at)
      : undefined,
    status: index === 0 ? "emerging" : "amplifying",
    confidence: toConfidenceLabel(item.confidence_score),
    sourceCount: docs.length,
    counterSourceCount: docs.length,
    receiptCount: Math.max(docs.length, 1),
    summary: item.summary,
    sources: docs.map(toSource),
    receipts: docs.length > 0 ? docs.map((document) => toCounterReceipt(item, document)) : [],
  };
}

function buildEdges(
  timelineNodes: InvestigationNode[],
  counterNodes: InvestigationNode[],
  currentNodeId: string,
  workspace: LiveInvestigationWorkspace,
) {
  const edges: InvestigationEdge[] = [];

  for (let index = 0; index < timelineNodes.length - 1; index += 1) {
    const source = timelineNodes[index];
    const target = timelineNodes[index + 1];
    edges.push({
      id: `edge-${source.id}-${target.id}`,
      source: source.id,
      target: target.id,
      edgeType: "temporal_sequence",
      label: "Later pickup",
      evidenceText: target.summary,
      confidence: target.confidence,
      animated: true,
    });
  }

  const finalTimelineNodeId =
    timelineNodes[timelineNodes.length - 1]?.id ?? null;
  if (finalTimelineNodeId) {
    edges.push({
      id: `edge-${finalTimelineNodeId}-${currentNodeId}`,
      source: finalTimelineNodeId,
      target: currentNodeId,
      edgeType: "temporal_sequence",
      label: "Current state",
      evidenceText:
        workspace.report?.sections.timeline_summary ??
        workspace.timeline?.timeline_summary,
      confidence:
        workspace.report?.confidence_label ??
        workspace.timeline?.confidence_label ??
        "unknown",
      animated: true,
    });
  }

  const counterAnchorNodeId = getCounterAnchorNodeId(timelineNodes);
  for (const node of counterNodes) {
    edges.push({
      id: `edge-${counterAnchorNodeId ?? currentNodeId}-${node.id}`,
      source: counterAnchorNodeId ?? currentNodeId,
      target: node.id,
      edgeType: "counter_narrative",
      label: "Counter-frame emerges",
      evidenceText: node.summary,
      confidence: node.confidence,
      animated: true,
    });
  }

  return edges;
}

function buildCurrentSources(
  workspace: LiveInvestigationWorkspace,
  docsById: Map<string, LiveDocument>,
  counterpoint?: LiveClaimCounterpointPair | null,
) {
  const topDocIds =
    workspace.retrieval?.high_relevance_document_ids ??
    workspace.retrieval?.retrieved_document_ids ??
    [];

  const byDocId = new Map<string, LiveDocument>();
  for (const docId of topDocIds.slice(0, 12)) {
    const document = docsById.get(docId);
    if (document) {
      byDocId.set(docId, document);
    }
  }

  for (const citation of workspace.report?.evidence_packet ?? []) {
    const document = docsById.get(citation.document_id);
    if (document) {
      byDocId.set(document.id, document);
    }
  }

  return [
    ...Array.from(byDocId.values()).slice(0, 12).map(toSource),
    ...buildCounterSourcesFromPair(counterpoint ?? null),
  ];
}

function buildCurrentReceipts(
  workspace: LiveInvestigationWorkspace,
  counterpoint?: LiveClaimCounterpointPair | null,
) {
  const receipts: InvestigationReceipt[] = [];

  for (const claim of workspace.report?.key_claims ?? []) {
    const primaryReceipt = claim.supporting_receipts[0];
    const primaryCitation = claim.citations[0];
    const supportStatus = claim.support_status
      ? claim.support_status.replaceAll("_", " ")
      : claim.claim_type;
    receipts.push({
      id: claim.claim_id,
      claimId: claim.claim_id,
      quoteOrSnippet:
        primaryReceipt?.evidence_span ??
        primaryReceipt?.snippet ??
        primaryCitation?.snippet ??
        claim.claim_text,
      sourceName: primaryReceipt?.source_name ?? primaryCitation?.source_name ?? "RhetoriQ report",
      supportReason:
        primaryReceipt?.support_reason ??
        primaryCitation?.relevance_note ??
        `${supportStatus} claim extracted from the report evidence packet.`,
      title: primaryReceipt?.title ?? primaryCitation?.title ?? claim.claim_text,
      url: primaryReceipt?.url ?? primaryCitation?.url,
      browserVerified: primaryReceipt?.verification_status === "verified",
    });
  }

  return [...receipts, ...buildCounterReceiptsFromPair(counterpoint ?? null)].slice(0, 8);
}

function toSource(document: LiveDocument) {
  return {
    id: document.id,
    name: document.source_name,
    publishedAt: formatDateTime(document.published_at),
    snippet: document.snippet ?? undefined,
    stance: "supporting" as const,
    title: document.title,
    type: document.source_type.replaceAll("_", " "),
    url: document.url,
  };
}

function toReceipt(event: LiveTimelineEvent, document?: LiveDocument): InvestigationReceipt {
  return {
    id: `receipt-${event.id}`,
    quoteOrSnippet: event.snippet ?? document?.snippet ?? event.explanation,
    sourceName: event.source_name,
    supportReason: event.explanation,
    title: event.title,
    url: event.url,
  };
}

function toCounterReceipt(
  item: LiveCounterNarrative,
  document: LiveDocument,
): InvestigationReceipt {
  return {
    id: `receipt-counter-${item.id}-${document.id}`,
    quoteOrSnippet: document.snippet ?? item.summary,
    sourceName: document.source_name,
    supportReason: item.summary,
    title: document.title,
    url: document.url,
  };
}

function getTimelineNodeType(event: LiveTimelineEvent): InvestigationNode["nodeType"] {
  if (event.event_type === "first_observed") {
    return "first_observed";
  }
  if (event.event_type === "official_mention") {
    return "official_mention";
  }
  if (event.narrative_side === "counter" || event.event_type === "counter_narrative_entry") {
    return "counter_narrative";
  }
  if (event.event_type === "broader_pickup") {
    return "media_pickup";
  }
  if (event.event_type === "resurfacing") {
    return "related";
  }
  return "amplification";
}

function getTimelineDisplayLabel(event: LiveTimelineEvent, index: number) {
  if (event.event_type === "first_observed") {
    return "First observed in our dataset";
  }

  if (event.source_type === "community_post") {
    return "Community amplification";
  }

  if (event.source_type === "local_news") {
    return "Local / news pickup";
  }

  if (event.source_type === "national_news") {
    return "National pickup";
  }

  if (event.event_type === "official_mention") {
    return "Official mention";
  }

  if (index === 1) {
    return "Community amplification";
  }

  return event.title;
}

function getTimelineDisplaySubtitle(event: LiveTimelineEvent) {
  const publishedAt = formatDateTime(event.timestamp);
  return publishedAt
    ? `${event.source_name} · ${publishedAt}`
    : event.source_name;
}

function getCounterAnchorNodeId(timelineNodes: InvestigationNode[]) {
  const localNewsNode = timelineNodes.find((node) => node.label === "Local / news pickup");
  if (localNewsNode) {
    return localNewsNode.id;
  }

  const nationalNode = timelineNodes.find((node) => node.label === "National pickup");
  if (nationalNode) {
    return nationalNode.id;
  }

  return timelineNodes[timelineNodes.length - 1]?.id ?? null;
}

function getFirstObservedLabel(workspace: LiveInvestigationWorkspace) {
  const firstDocId = workspace.timeline?.first_observed_doc_id;
  if (!firstDocId) {
    return "First observed source not established yet";
  }

  const firstDoc = workspace.retrieved_documents.find(
    (document) => document.id === firstDocId,
  );
  if (!firstDoc) {
    return "First observed source captured in the investigation timeline";
  }

  const publishedAt = formatDateTime(firstDoc.published_at);
  return publishedAt
    ? `${firstDoc.source_name}, ${publishedAt}`
    : firstDoc.source_name;
}

function countWorkspaceReceipts(workspace: LiveInvestigationWorkspace) {
  const ids = new Set<string>();

  for (const claim of workspace.report?.key_claims ?? []) {
    if (claim.supporting_receipts.length > 0 || claim.contradicting_receipts.length > 0) {
      for (const receipt of [...claim.supporting_receipts, ...claim.contradicting_receipts]) {
        ids.add(`claim-${claim.claim_id}-${receipt.document_id}`);
      }
      continue;
    }
    ids.add(claim.claim_id);
  }

  for (const event of workspace.timeline?.timeline_events ?? []) {
    ids.add(event.id);
  }

  for (const pair of workspace.claim_counterpoints?.pairs ?? []) {
    for (const receipt of pair.counter_receipts) {
      ids.add(`counter-${pair.claim_id}-${receipt.document_id}`);
    }
  }

  return ids.size;
}

function formatStatus(status: LiveInvestigationWorkspace["status"]) {
  switch (status) {
    case "planning_completed":
      return "Planning completed";
    case "retrieval_completed":
      return "Retrieval completed";
    case "source_diversity_completed":
      return "Source diversity built";
    case "timeline_completed":
      return "Timeline built";
    case "counter_narrative_completed":
      return "Counter-narratives built";
    case "narrative_family_completed":
      return "Narrative family built";
    case "gap_analysis_completed":
      return "Gap analysis built";
    case "provenance_completed":
      return "Provenance traced";
    case "analyst_completed":
      return "Analyst synthesis built";
    case "skeptic_completed":
      return "Skeptic review built";
    case "claim_counterpoint_completed":
      return "Claim counterpoints built";
    case "receipts_completed":
      return "Receipts built";
    case "agent_debate_completed":
      return "Agent debate built";
    case "report_completed":
      return "Final report built";
    case "research_loop_completed":
      return "Research loop completed";
    default:
      return "In progress";
  }
}

function toDisplayConfidence(confidence: InvestigationConfidence) {
  switch (confidence) {
    case "high":
      return "High";
    case "medium":
      return "Medium";
    case "low":
      return "Low";
    default:
      return "Low";
  }
}

function toConfidenceLabel(score: number): InvestigationConfidence {
  if (score >= 0.75) {
    return "high";
  }
  if (score >= 0.45) {
    return "medium";
  }
  return "low";
}

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return undefined;
  }

  return DATE_TIME_FORMATTER.format(new Date(value));
}

function formatTime(value: string | null | undefined) {
  if (!value) {
    return undefined;
  }

  return TIME_FORMATTER.format(new Date(value));
}

function summarizeDistribution(distribution: Record<string, number>) {
  const entries = Object.entries(distribution)
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1]);
  if (entries.length === 0) {
    return "";
  }
  const [label, count] = entries[0];
  return `${count} ${label.replaceAll("_", " ")}`;
}

function getStrongestCounterpointPair(
  workspace: LiveInvestigationWorkspace,
): LiveClaimCounterpointPair | null {
  const pairs = workspace.claim_counterpoints?.pairs ?? [];
  if (pairs.length === 0) {
    return null;
  }
  return [...pairs].sort((left, right) => right.confidence_score - left.confidence_score)[0];
}

function getBestCounterpointPairForDocumentIds(
  workspace: LiveInvestigationWorkspace,
  documentIds: string[],
): LiveClaimCounterpointPair | null {
  const pairs = workspace.claim_counterpoints?.pairs ?? [];
  if (pairs.length === 0 || documentIds.length === 0) {
    return null;
  }

  const wanted = new Set(documentIds);
  const overlapping = pairs.filter((pair) =>
    pair.supporting_document_ids.some((docId) => wanted.has(docId)),
  );
  if (overlapping.length === 0) {
    return null;
  }
  return overlapping.sort((left, right) => right.confidence_score - left.confidence_score)[0];
}

function buildCounterSourcesFromPair(
  pair: LiveClaimCounterpointPair | null,
): InvestigationNodeSource[] {
  if (!pair) {
    return [];
  }

  return pair.counter_receipts.map((receipt) => ({
    id: `counter-source-${pair.claim_id}-${receipt.document_id}`,
    name: receipt.source_name,
    type: `${pair.counter_type} counterpoint`,
    title: receipt.title,
    url: receipt.url,
    publishedAt: formatDateTime(receipt.published_at),
    snippet: receipt.snippet ?? pair.counter_claim_text,
    stance: "opposing",
    counterType: pair.counter_type,
  }));
}

function buildCounterReceiptsFromPair(
  pair: LiveClaimCounterpointPair | null,
): InvestigationReceipt[] {
  if (!pair) {
    return [];
  }

  return pair.counter_receipts.map((receipt) => ({
    id: `counter-receipt-${pair.claim_id}-${receipt.document_id}`,
    claimId: pair.claim_id,
    quoteOrSnippet: receipt.snippet ?? pair.counter_claim_text,
    sourceName: receipt.source_name,
    supportReason: `${pair.counter_type.replaceAll("_", " ")} counterpoint. ${receipt.relevance_note}`,
    title: receipt.title,
    url: receipt.url,
  }));
}
