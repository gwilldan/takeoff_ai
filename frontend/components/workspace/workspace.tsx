"use client"
import { PDFDocumentProxy } from "pdfjs-dist"
import { PdfPageSize } from "../pdf/use-pdf-document"
import { LayerSidebar } from "./layer-sidebar"
import { PdfViewer, PdfViewerProps } from "./pdf-viewer"
import {TopBarProps, TopBar} from "./top-bar"

export default function Workspace() {

  const topBarProps: TopBarProps = {
    fileName: "okon",
    onOpenFile: () => {},
    scaleText: "100%",
  }

  const pdfViewerProps: PdfViewerProps = {
    onAvailableWidth: () => {},
    onZoomBy: () => {},
    pageNumber: 1,
    pageSize: {height: 100, width: 100} as PdfPageSize,
    pdfDocument: "" as unknown as PDFDocumentProxy,
    zoom: 100,
  }

  return (
    <div className="flex h-screen flex-col">
      <TopBar {...topBarProps} />
      <div className="relative grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)_220px]">
        <LayerSidebar />
        <PdfViewer  {...pdfViewerProps} />
      </div>
    </div>
  )
}
