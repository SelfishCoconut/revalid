import type { ReactElement, ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function Providers({ route, children }: { route: string; children: ReactNode }) {
  return (
    <QueryClientProvider client={makeClient()}>
      <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

/** Render a UI tree wrapped in a fresh QueryClient and a MemoryRouter. */
export function renderWithProviders(ui: ReactElement, route = "/") {
  return render(<Providers route={route}>{ui}</Providers>);
}
