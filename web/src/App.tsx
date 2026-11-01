import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { ToastProvider } from "./components/Toast";
import { AgentUiProvider } from "./layout/AgentUi";
import { BinRoute } from "./screens/Bin";
import { ClipperRoute } from "./screens/Clipper";
import { HomeRoute } from "./screens/Home";
import { ProjectRoute } from "./screens/Project";
import { NewProjectRoute, ProjectSettingsRoute } from "./screens/ProjectForm";
import { SequenceRoute } from "./screens/Sequence";
import { SequenceAddRoute } from "./screens/SequenceAdd";
import { SequenceExportRoute } from "./screens/SequenceExport";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: true } },
});

function LibraryRedirect() {
  const { slug } = useParams();
  return <Navigate to={`/p/${encodeURIComponent(slug ?? "")}`} replace />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AgentUiProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<HomeRoute />} />
              <Route path="/new" element={<NewProjectRoute />} />
              <Route path="/p/:slug" element={<ProjectRoute />} />
              <Route path="/p/:slug/settings" element={<ProjectSettingsRoute />} />
              {/* the project screen is the explorer on a phone; the sidebar is on wide */}
              <Route path="/p/:slug/library" element={<LibraryRedirect />} />
              <Route path="/p/:slug/b/:binId" element={<BinRoute />} />
              <Route path="/p/:slug/b/:binId/clip/:mediaId" element={<ClipperRoute />} />
              <Route path="/p/:slug/s/:seqId" element={<SequenceRoute />} />
              <Route path="/p/:slug/s/:seqId/add" element={<SequenceAddRoute />} />
              <Route path="/p/:slug/s/:seqId/export" element={<SequenceExportRoute />} />
              {/* the clipper in sequence mode: trims one item, the bin's take stays whole */}
              <Route path="/p/:slug/s/:seqId/clip/:binId" element={<ClipperRoute />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
        </AgentUiProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
