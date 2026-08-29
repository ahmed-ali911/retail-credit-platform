import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAuth } from "./auth/RequireAuth";
import { Shell } from "./components/Shell";
import { LoginPage } from "./pages/LoginPage";
import { DashboardPage } from "./pages/DashboardPage";
import { CreateCustomerPage } from "./pages/CreateCustomerPage";
import { CreateProductPage } from "./pages/CreateProductPage";
import { NewApplicationPage } from "./pages/NewApplicationPage";
import { OfferPage } from "./pages/OfferPage";
import { ContractPage } from "./pages/ContractPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Shell />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="customers/new" element={<CreateCustomerPage />} />
        <Route path="products/new" element={<CreateProductPage />} />
        <Route path="applications/new" element={<NewApplicationPage />} />
        <Route path="applications/:applicationId/offer" element={<OfferPage />} />
        <Route path="offers/:offerId" element={<OfferPage />} />
        <Route path="contracts/:contractId" element={<ContractPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
