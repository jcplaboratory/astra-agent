import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Astra Operator Console",
  description: "Control-plane operations for Astra Agent"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
