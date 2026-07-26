// Research mode shell: header, degraded banner, blocking fault screens, and
// whichever stage is current.
//
// WHY this replaces ChatView entirely rather than sitting inside it: the whole
// point of the mode is that an investigation is not a conversation. Keeping a
// composer at the bottom would keep telling the user "type a message", which is
// the wrong mental model for something that runs for four minutes.
import React, { useEffect } from "react";
import { Search, FlaskConical, WifiOff, SearchX, TriangleAlert, AlertCircle } from "lucide-react";
import { useResearch } from "../../stores/research";
import { useBackend } from "../../stores/backend";
import ResearchHome from "./ResearchHome";
import ResearchPlan from "./ResearchPlan";
import ResearchRun from "./ResearchRun";
import ResearchReport from "./ResearchReport";

export default function ResearchView({ onOpenIntegrations }) {
  const stage = useResearch((s) => s.stage);
  const fault = useResearch((s) => s.fault);
  const faultDetail = useResearch((s) => s.faultDetail);
  const degraded = useResearch((s) => s.degraded);
  const lanes = useResearch((s) => s.lanes);
  const evidence = useResearch((s) => s.evidence);
  const { newInvestigation, clearFault, dismissDegraded, setDegraded } = useResearch();
  const status = useBackend((s) => s.status);

  // Docker off is a degradation, not a failure: search snippets still work, the
  // pages just do not get read in full. Saying so up front beats silently
  // producing a thinner report and letting the user wonder why.
  useEffect(() => {
    if (status && status.docker === false) setDegraded(true);
  }, [status, setDegraded]);

  return (
    <div className="research">
      <div className="research-header">
        <Search size={16} strokeWidth={1.8} />
        <h2>Research</h2>
        <button className="btn small ghost" onClick={newInvestigation}>New investigation</button>
      </div>

      {degraded && (
        <div className="research-degraded">
          <AlertCircle size={15} strokeWidth={1.8} />
          <span>
            Docker is off. Page fetching is sandboxed, so Arthur will use search snippets only:
            extracts will be shorter and some sources unreadable.
          </span>
          <button className="btn tiny ghost" onClick={dismissDegraded}>Dismiss</button>
        </div>
      )}

      {fault === "tavily" && (
        <Fault
          icon={<FlaskConical size={21} strokeWidth={1.7} />}
          title="Research requires a Tavily key"
          body="Web search runs through Tavily. Add a key in Integrations to enable Research mode. Every other mode continues to work without it."
          primary={{ label: "Open Integrations", onClick: onOpenIntegrations }}
        />
      )}

      {fault === "offline" && (
        <Fault
          icon={<WifiOff size={21} strokeWidth={1.7} />}
          title="No network connection"
          body="Research needs the internet to search and read sources. Your local models, memory and past reports remain fully available offline."
          secondary={{ label: "Retry connection", onClick: clearFault }}
        />
      )}

      {fault === "zero" && (
        <Fault
          icon={<SearchX size={21} strokeWidth={1.7} />}
          title="No sources found"
          body="Every sub-question returned empty. The question may be too narrow, too recent, or the domain filters may be excluding everything relevant."
          primary={{ label: "Revise the plan", onClick: () => { clearFault(); useResearch.setState({ stage: "plan" }); } }}
          secondary={{ label: "Widen sources", onClick: () => { clearFault(); useResearch.setState({ stage: "home" }); } }}
        />
      )}

      {fault === "failed" && (
        <div className="research-scroll">
          <div className="research-col">
            <div className="research-failed">
              <TriangleAlert size={16} strokeWidth={1.8} />
              <div>
                <div className="research-failed-title">
                  Run stopped after {lanes.filter((l) => ["done", "thin"].includes(l.state)).length} of {lanes.length} sub-questions
                </div>
                <div className="research-failed-body">
                  {faultDetail || "The search provider returned repeated errors."} Everything gathered
                  before the failure has been kept.
                </div>
              </div>
            </div>
            <div className="micro-label">Preserved</div>
            <div className="research-preserved">
              <span>{lanes.filter((l) => l.state === "done").length} sub-questions answered</span>
              <span>{evidence.length} sources extracted</span>
              <span>{evidence.filter((e) => e.contradicts).length} contradictions flagged</span>
            </div>
            <div className="research-actions">
              <button className="btn primary" onClick={() => { clearFault(); useResearch.getState().run(); }}>Resume run</button>
              <button className="btn" onClick={clearFault}>Keep what is here</button>
            </div>
          </div>
        </div>
      )}

      {!fault && stage === "home" && <ResearchHome />}
      {!fault && stage === "plan" && <ResearchPlan />}
      {!fault && stage === "run" && <ResearchRun />}
      {!fault && stage === "report" && <ResearchReport />}
    </div>
  );
}

function Fault({ icon, title, body, primary, secondary }) {
  return (
    <div className="research-fault">
      <div className="research-fault-inner">
        <div className="research-fault-icon">{icon}</div>
        <h3>{title}</h3>
        <p>{body}</p>
        <div className="research-actions center">
          {primary && <button className="btn primary" onClick={primary.onClick}>{primary.label}</button>}
          {secondary && <button className="btn" onClick={secondary.onClick}>{secondary.label}</button>}
        </div>
      </div>
    </div>
  );
}
