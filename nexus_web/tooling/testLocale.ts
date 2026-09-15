import { beforeEach } from "vitest";
import { setLocale } from "../src/localization/locale";

// Existing presentation assertions describe the English language explicitly.
setLocale("en-US");
beforeEach(() => setLocale("en-US"));
