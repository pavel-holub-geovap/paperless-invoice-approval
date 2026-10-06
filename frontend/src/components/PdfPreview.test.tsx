import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PdfPreview } from "./PdfPreview";

const pdf = vi.hoisted(() => ({
  getDocument: vi.fn(), destroy: vi.fn().mockResolvedValue(undefined),
  getPage: vi.fn(), cancel: vi.fn(),
}));
vi.mock("pdfjs-dist", () => ({GlobalWorkerOptions: {}, getDocument: pdf.getDocument}));

beforeEach(() => {
  pdf.getPage.mockResolvedValue({
    getViewport: () => ({width:600,height:800}),
    render: () => ({promise:Promise.resolve(),cancel:pdf.cancel}),
  });
  pdf.getDocument.mockReturnValue({promise:Promise.resolve({numPages:2,getPage:pdf.getPage}),destroy:pdf.destroy});
});

describe("portable PDF preview", () => {
  it("renders and navigates real pages independently of the native PDF plugin", async () => {
    const view = render(<PdfPreview url="/api/invoices/fixture/pdf"/>);
    await waitFor(() => expect(screen.getByRole("img",{name:"Originální faktura, strana 1"})).toBeVisible());
    expect(pdf.getDocument).toHaveBeenCalledWith({url:"/api/invoices/fixture/pdf",withCredentials:true});
    expect(screen.getByTitle("Originální faktura")).not.toBeVisible();
    fireEvent.click(screen.getByRole("button",{name:"Další strana"}));
    await waitFor(() => expect(screen.getByRole("img",{name:"Originální faktura, strana 2"})).toBeVisible());
    expect(pdf.getPage).toHaveBeenCalledWith(2);
    expect(screen.getByRole("link")).toHaveAttribute("href","/api/invoices/fixture/pdf");
    view.unmount(); expect(pdf.destroy).toHaveBeenCalled();
  });
  it("keeps an explicit original link and native fallback on a render failure", async () => {
    pdf.getDocument.mockReturnValue({promise:Promise.reject(new Error("unavailable")),destroy:pdf.destroy});
    render(<PdfPreview url="/api/invoices/fixture/pdf"/>);
    await waitFor(() => expect(screen.getByTitle("Originální faktura")).toBeVisible());
    expect(screen.getByText(/Náhled nelze vykreslit/)).toBeVisible();
  });
  it("does not request a missing Paperless source", () => {
    const before = pdf.getDocument.mock.calls.length;
    render(<PdfPreview url="/missing" missing/>);
    expect(screen.getByText(/již není v Paperless/)).toBeVisible();
    expect(pdf.getDocument.mock.calls.length).toBe(before);
  });
});
