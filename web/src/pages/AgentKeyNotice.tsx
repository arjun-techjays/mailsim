import { Link } from 'react-router-dom';
import { usePageTitle } from '../hooks/usePageTitle';

/**
 * Agent keys belong to the agent under test. Using one here would make the human operator act
 * as the agent: reads are recorded as agent actions and can arm after_agent_action triggers,
 * changing the run being evaluated. The dashboard therefore makes no data requests with them.
 */
export function AgentKeyNotice() {
  usePageTitle('Agent key');
  return (
    <section aria-labelledby="agent-key-h" className="card warning" data-testid="agent-key-notice">
      <h1 id="agent-key-h">This is an agent key</h1>
      <p>
        Agent keys belong to the agent under test. Anything done with them is recorded as the agent’s own activity
        and can change the run being evaluated, so the dashboard does not use them.
      </p>
      <p>
        <Link to="/connect">Connect with a controller or evaluator key</Link> to inspect and control runs.
      </p>
    </section>
  );
}
