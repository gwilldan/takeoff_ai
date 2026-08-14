"use client"
import {TopBarProps, TopBar} from "./top-bar"

export default function Workspace() {

  const topBarProps: TopBarProps = {
    fileName: "okon",
    onOpenFile: () => {},
    scaleText: "100%",
  }

  return (
    <>
      <TopBar {...topBarProps} />
      <p>Workspace</p>
    </>
  )
}
