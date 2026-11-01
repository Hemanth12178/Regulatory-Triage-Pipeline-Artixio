import { Navigate, Route, Routes } from "react-router-dom";
import HomePage from "./pages/HomePage";
import SamplePage from "./pages/SamplePage";
import TriagePage from "./pages/TriagePage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/triage" element={<TriagePage />} />
      <Route path="/sample" element={<SamplePage />} />
      {/* Anything else lands on the overview rather than a blank screen. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
