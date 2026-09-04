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
import { CustomerPage } from "./pages/CustomerPage";
import { CustomerDirectoryPage } from "./pages/CustomerDirectoryPage";
import { ProductDirectoryPage } from "./pages/ProductDirectoryPage";
import { ContractDirectoryPage } from "./pages/ContractDirectoryPage";
import { ReviewQueuePage, ReviewApplicationPage } from "./pages/ReviewQueuePage";
import { ReconciliationPage } from "./pages/ReconciliationPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { ConfigPage } from "./pages/ConfigPage";
import { AuditLogPage } from "./pages/AuditLogPage";
import { CollectionsPage, CollectionCasePage } from "./pages/CollectionsPage";
import { SnapshotPage } from "./pages/SnapshotPage";
import { InventoryPage } from "./pages/InventoryPage";
import { ReportsPage } from "./pages/ReportsPage";

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
        <Route path="customers" element={<CustomerDirectoryPage />} />
        <Route path="customers/new" element={<CreateCustomerPage />} />
        <Route path="customers/:customerId" element={<CustomerPage />} />
        <Route path="products" element={<ProductDirectoryPage />} />
        <Route path="products/new" element={<CreateProductPage />} />
        <Route path="applications/new" element={<NewApplicationPage />} />
        <Route path="applications/:applicationId/offer" element={<OfferPage />} />
        <Route path="offers/:offerId" element={<OfferPage />} />
        <Route path="contracts" element={<ContractDirectoryPage />} />
        <Route path="contracts/:contractId" element={<ContractPage />} />
        <Route path="review" element={<ReviewQueuePage />} />
        <Route path="review/:applicationId" element={<ReviewApplicationPage />} />
        <Route path="reconciliation" element={<ReconciliationPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="collections" element={<CollectionsPage />} />
        <Route path="collections/:caseId" element={<CollectionCasePage />} />
        <Route path="snapshot" element={<SnapshotPage />} />
        <Route path="inventory" element={<InventoryPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="config" element={<ConfigPage />} />
        <Route path="audit" element={<AuditLogPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
