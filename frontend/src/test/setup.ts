// Adds DOM matchers such as toBeInTheDocument() to every test.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library only cleans up after each test by itself when the test runner's globals are on.
// They are off here, so do it explicitly: otherwise screens from earlier tests pile up.
afterEach(cleanup);
