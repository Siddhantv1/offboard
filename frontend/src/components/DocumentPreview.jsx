/** Show one uploaded document, whatever it turned out to be.
 *
 * An `<img>` was the only viewer in the app, and that is most of the reason
 * only images were accepted: somebody holding a signed PDF was being asked to
 * photograph their screen. A PDF gets the browser's own viewer in a frame; an
 * HTML file gets a download link and deliberately nothing else, because the
 * server refuses to serve it inline -- rendering an uploaded page on this
 * origin, with the reader's session attached, is stored XSS.
 */
const IMAGE = /^image\//i;
const PDF = /^application\/pdf$/i;

export default function DocumentPreview({ src, contentType = "", filename, label }) {
  const type = String(contentType).split(";")[0].trim();

  if (IMAGE.test(type)) {
    return (
      <div className="doc-scan">
        <img src={src} alt={label} />
      </div>
    );
  }

  if (PDF.test(type)) {
    return (
      <div className="doc-scan">
        {/* An <object> rather than an <iframe>: if the browser has no PDF
            viewer it renders the fallback inside instead of an empty box. */}
        <object className="doc-pdf" data={src} type="application/pdf"
                aria-label={label}>
          <p className="doc-blurb">
            Your browser will not display this PDF.{" "}
            <a href={src} download={filename}>Download it</a> to read it.
          </p>
        </object>
        <p className="doc-sent">
          <a href={src} target="_blank" rel="noreferrer">Open in a new tab</a>
          {" · "}
          <a href={src} download={filename}>Download</a>
        </p>
      </div>
    );
  }

  return (
    <p className="doc-blurb">
      {`${filename || "This file"} is not something the browser can show here. `}
      <a href={src} download={filename}>Download it</a> to read it.
    </p>
  );
}
