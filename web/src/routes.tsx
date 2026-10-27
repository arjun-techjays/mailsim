import type { ReactNode } from 'react';
import type { RouteObject } from 'react-router-dom';
import { RequireSession } from './components/Gates';
import { Layout } from './components/Layout';
import { AgentKeyNotice } from './pages/AgentKeyNotice';
import { ComparePage } from './pages/ComparePage';
import { ConnectPage } from './pages/ConnectPage';
import { EvaluationPage } from './pages/EvaluationPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { RunDetailPage } from './pages/RunDetailPage';
import { RunsPage } from './pages/RunsPage';
import { ScenarioDetailPage } from './pages/ScenarioDetailPage';
import { ScenariosPage } from './pages/ScenariosPage';
import { StartRunPage } from './pages/StartRunPage';
// ---- connect & guide (UX-F3)
import { ConceptsPage } from './pages/ConceptsPage';
import { ConnectAgentPage } from './pages/ConnectAgentPage';
import { HomePage } from './pages/HomePage';
// ---- builder (UX-F1)
import { ScenarioBuilderPage } from './builder/ScenarioBuilderPage';
// ---- end builder (UX-F1)
import { ImportPackPage } from './pack/ImportPackPage';
// ---- answer keys & mailbox browser (UX-F2)
import { AnswerKeyEditorPage } from './answerkey/AnswerKeyEditorPage';

const guarded = (el: ReactNode) => <RequireSession>{el}</RequireSession>;

export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { index: true, element: <HomePage /> }, // UX-F3: getting started (was HomeRedirect)
      { path: 'connect', element: <ConnectPage /> },
      { path: 'scenarios', element: guarded(<ScenariosPage />) },
      // ---- builder (UX-F1)
      { path: 'scenarios/new', element: guarded(<ScenarioBuilderPage mode="new" />) },
      { path: 'scenarios/import', element: guarded(<ImportPackPage />) },
      { path: 'scenarios/:key/edit', element: guarded(<ScenarioBuilderPage mode="edit" />) },
      { path: 'scenarios/:key/clone', element: guarded(<ScenarioBuilderPage mode="clone" />) },
      // ---- end builder (UX-F1)
      { path: 'scenarios/:key', element: guarded(<ScenarioDetailPage />) },
      { path: 'scenarios/:key/start', element: guarded(<StartRunPage />) },
      { path: 'scenarios/:key/compare', element: guarded(<ComparePage />) },
      // ---- answer keys & mailbox browser (UX-F2)
      { path: 'scenarios/:key/answer-key', element: guarded(<AnswerKeyEditorPage />) },
      { path: 'runs', element: guarded(<RunsPage />) },
      { path: 'runs/:runId', element: guarded(<RunDetailPage />) },
      { path: 'runs/:runId/evaluations', element: guarded(<EvaluationPage />) },
      { path: 'agent-key', element: <AgentKeyNotice /> },
      // ---- connect & guide (UX-F3)
      { path: 'concepts', element: <ConceptsPage /> },
      { path: 'runs/:runId/connect', element: guarded(<ConnectAgentPage />) },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];
