"""All documents are generated in memory; no work documents are loaded."""
from io import BytesIO
from zipfile import ZipFile
from pathlib import Path
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.document_reading import read_document, DocumentReadError


def pdf_bytes(text_pages):
    writer=PdfWriter()
    for text in text_pages:
        page=writer.add_blank_page(width=300, height=400)
        if text:
            font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),
                NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream=DecodedStreamObject();stream.set_data(f'BT /F1 12 Tf 30 300 Td ({text}) Tj ET'.encode())
            page[NameObject('/Contents')]=writer._add_object(stream)
    out=BytesIO();writer.write(out);return out.getvalue()


@pytest.mark.parametrize('pages,calls', [(['Synthetic document readable text enough for direct reading'],0),
    ([None],1),(['Synthetic document readable text enough for direct reading',None],1)])
def test_pdf_reads_only_pages_without_text(tmp_path,pages,calls):
    target=tmp_path/'synthetic.pdf';target.write_bytes(pdf_bytes(pages));seen=[]
    def ocr(path):
        assert Path(path).is_file();seen.append(1);return '합성 스캔 문서 글자'
    result=read_document(target, '.pdf', ocr_page=ocr)
    assert len(seen)==calls
    assert result.text and len(result.locations)==len(pages)
    assert result.ocr_page_count==calls


def test_scanned_document_page_does_not_inherit_photo_band_requests(tmp_path,monkeypatch):
    from app import ocr, image_ocr_runtime
    from app.config import settings
    from app.document_reading import read_scanned_page
    monkeypatch.setattr(settings,'ocr_image_bands',3)
    monkeypatch.setattr(image_ocr_runtime,'image_provider',lambda:'ollama')
    calls=[]
    monkeypatch.setattr(ocr,'_ollama_extract_single',lambda *a,**k:calls.append(k) or '합성 문서 200ml')
    assert read_scanned_page(tmp_path/'synthetic.png')=='합성 문서 200ml'
    assert len(calls)==1 and calls[0]['prompt_override']
    assert settings.ocr_image_bands==3


@pytest.mark.parametrize('kind',['no_text','connection','blocked'])
def test_scanned_page_distinguishes_empty_model_and_safety_failure(tmp_path,kind):
    from app.image_ocr_runtime import OcrError, ImageOcrFailure
    target=tmp_path/'synthetic.pdf';target.write_bytes(pdf_bytes([None]))
    def ocr(_):
        if kind=='no_text':raise OcrError('이미지에서 판독된 글이 없습니다.')
        if kind=='blocked':raise OcrError('비정상 반복 출력')
        raise ImageOcrFailure('image_model_unavailable','합성 연결 실패')
    if kind=='no_text':
        result=read_document(target,'.pdf',ocr_page=ocr)
        assert result.text=='' and result.ocr_page_count==1
    else:
        expected='output_blocked' if kind=='blocked' else 'image_model_unavailable'
        with pytest.raises(DocumentReadError,match=expected):read_document(target,'.pdf',ocr_page=ocr)


@pytest.mark.parametrize('extension', ['.txt','.csv'])
def test_plain_text_keeps_korean_and_locations(tmp_path,extension):
    target=tmp_path/('synthetic'+extension);target.write_text('합성 항목,값\n물,200ml',encoding='utf-8')
    result=read_document(target,extension)
    assert '200ml' in result.text and result.locations


def test_docx_xml_is_read_as_text(tmp_path):
    target=tmp_path/'synthetic.docx'
    with ZipFile(target,'w') as z:
        z.writestr('[Content_Types].xml','<Types>wordprocessingml document</Types>')
        z.writestr('_rels/.rels','<Relationships/>')
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>합성 문서입니다</w:t></w:r></w:p></w:body></w:document>')
    assert read_document(target,'.docx').text=='합성 문서입니다'


@pytest.mark.parametrize('extension,content,code',[('.pdf',b'%PDF-corrupt','corrupt_document'),
    ('.docx',b'PKfake','invalid_document'),('.txt',b'','empty_document')])
def test_safe_failures(tmp_path,extension,content,code):
    target=tmp_path/('synthetic'+extension);target.write_bytes(content)
    with pytest.raises(DocumentReadError) as error:read_document(target,extension)
    assert error.value.code==code


def test_encrypted_pdf_is_not_opened(tmp_path):
    writer=PdfWriter();writer.add_blank_page(100,100);writer.encrypt('synthetic')
    target=tmp_path/'locked.pdf'
    with target.open('wb') as f:writer.write(f)
    with pytest.raises(DocumentReadError,match='encrypted_document'):read_document(target,'.pdf')


@pytest.mark.parametrize('extension,entries,expected',[
    ('.hwpx', {'mimetype':'application/hwp+zip','version.xml':'<version/>','Contents/content.hpf':'<package/>','Contents/header.xml':'<header/>','Contents/section0.xml':'<section><t>합성 한글 문서</t></section>'},'합성 한글 문서'),
    ('.xlsx', {'[Content_Types].xml':'<Types>spreadsheetml workbook</Types>','_rels/.rels':'<Relationships/>','xl/workbook.xml':'<workbook/>','xl/sharedStrings.xml':'<sst><si><t>합성 셀</t></si></sst>','xl/worksheets/sheet1.xml':'<worksheet><row r="1"><c t="s"><v>0</v></c><c><f>1+1</f><v>2</v></c></row></worksheet>'},'합성 셀 | 2'),
    ('.pptx', {'[Content_Types].xml':'<Types>presentationml presentation</Types>','_rels/.rels':'<Relationships/>','ppt/presentation.xml':'<presentation/>','ppt/slides/slide1.xml':'<slide><t>합성 발표자료</t></slide>'},'합성 발표자료'),
])
def test_optional_xml_formats_only_read_text_and_cached_values(tmp_path,extension,entries,expected):
    target=tmp_path/('synthetic'+extension)
    with ZipFile(target,'w') as z:
        for name,value in entries.items():z.writestr(name,value)
    result=read_document(target,extension)
    assert result.text==expected and result.locations
    assert result.ocr_page_count==0


def test_pdf_page_limit_and_text_character_limit(tmp_path):
    target=tmp_path/'many.pdf';target.write_bytes(pdf_bytes([None]*41))
    with pytest.raises(DocumentReadError,match='document_limit'):read_document(target,'.pdf')
    target=tmp_path/'large.txt';target.write_text('가'*100001,encoding='utf-8')
    with pytest.raises(DocumentReadError,match='document_limit'):read_document(target,'.txt')


@pytest.mark.parametrize('extension',['.hwp','.doc','.xls','.ppt'])
def test_legacy_formats_do_not_call_unverified_parsers(tmp_path,extension):
    target=tmp_path/('legacy'+extension);target.write_bytes(b'synthetic')
    with pytest.raises(DocumentReadError,match='unsupported_document'):read_document(target,extension)


def test_docx_external_entity_is_rejected(tmp_path):
    target=tmp_path/'entity.docx'
    with ZipFile(target,'w') as z:
        z.writestr('[Content_Types].xml','<Types>wordprocessingml document</Types>')
        z.writestr('_rels/.rels','<Relationships/>')
        z.writestr('word/document.xml','<!DOCTYPE document [<!ENTITY x SYSTEM "http://invalid.invalid/private">]><document>&x;</document>')
    with pytest.raises(DocumentReadError,match='invalid_document'):read_document(target,'.docx')
